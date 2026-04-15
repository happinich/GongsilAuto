"""
공실닷컴 매물 재등록 자동화 모듈

흐름:
  1. 최초등록일 기준 가장 오래된 매물 목록 가져오기
  2. 해당 매물 수정 페이지에서 전체 데이터 추출
  3. 신규매물등록 → 매물 유형 선택 → 빈 폼에 입력 → 등록
  4. 기존 매물 삭제
"""
import asyncio
import math
import re
from datetime import date
from pathlib import Path
from typing import Optional

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

MY_URL    = "https://www.gongsil.com/article/my/"
LOGIN_URL = "https://www.gongsil.com/h/member/login.php"

# bid(단지) 선택이 필요한 매물 유형 코드 (아파트, 오피스텔, 분양권, 재건축/개발)
NEEDS_BID = {11, 12, 13, 14, 21, 22}

# 자주 실행 대상: 아파트/오피스텔 + 전세/월세/단기
FREQUENT_CODES  = {11, 21}
FREQUENT_BTYPES = {"전세", "월세", "단기"}

def _is_frequent(lst: dict) -> bool:
    return lst["code"] in FREQUENT_CODES and lst["b_type"] in FREQUENT_BTYPES

def get_daily_per_run(total: int, runs_per_day: int = 5) -> int:
    """오늘 총 매물 수를 기준으로 1회 처리 수를 계산하고 파일에 저장/로드."""
    today = date.today().isoformat()
    plan_file = Path("logs/daily_plan.txt")
    try:
        if plan_file.exists():
            lines = plan_file.read_text().strip().split("\n")
            if len(lines) == 2 and lines[0] == today:
                per_run = int(lines[1])
                logger.info(f"오늘 계획 로드: {per_run}개/회 (총 {total}개)")
                return per_run
    except Exception:
        pass
    per_run = math.ceil(total / runs_per_day)
    try:
        plan_file.parent.mkdir(exist_ok=True)
        plan_file.write_text(f"{today}\n{per_run}")
    except Exception:
        pass
    logger.info(f"오늘 총 {total}개 매물 → {per_run}개/회 × {runs_per_day}회 계획 수립")
    return per_run


class GongsilManager:
    def __init__(
        self,
        username: str,
        password: str,
        page_id: str,
        headless: bool = True,
        max_per_run: Optional[int] = None,
    ):
        self.username    = username
        self.password    = password
        self.page_id     = page_id
        self.headless    = headless
        self.max_per_run = max_per_run
        self._playwright: Optional[Playwright]      = None
        self._browser:    Optional[Browser]         = None
        self._context:    Optional[BrowserContext]  = None
        self._page:       Optional[Page]            = None

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser    = await self._playwright.chromium.launch(headless=self.headless)
        self._context    = await self._browser.new_context()
        self._page       = await self._context.new_page()
        self._page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await self._login()
        return self

    async def __aexit__(self, *args):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ──────────────────────────────────────────────────────────────────────
    # 행 텍스트 → 식별자 (가변 부분 제거)
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _row_sig(row_text: str) -> str:
        """행 텍스트에서 순번·광고기간·날짜 제거 → 변하지 않는 식별자 반환"""
        t = re.sub(r'^\s*\d+\s*', '', row_text)   # 선행 순번
        t = re.sub(r'\d+\s*일\s*전', '', t)        # N일전
        t = re.sub(r'\d{2}\.\d{2}', '', t)         # MM.DD 날짜
        t = re.sub(r'광고\s*', '', t)              # 광고
        return ' '.join(t.split())

    # ──────────────────────────────────────────────────────────────────────
    # 로그인
    # ──────────────────────────────────────────────────────────────────────
    async def _login(self):
        page = self._page
        logger.info(f"로그인: {self.username}")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await page.fill("#login_id", self.username)
        await page.fill("#login_pw", self.password)
        await page.click("button.gs_btn_submit")
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        if "login" in page.url:
            raise RuntimeError("로그인 실패")
        logger.info("로그인 성공")

    # ──────────────────────────────────────────────────────────────────────
    # 매물 목록 (최초등록일 오름차순 = 오래된 것 먼저)
    # ──────────────────────────────────────────────────────────────────────
    async def _load_listings(self) -> list[dict]:
        page = self._page
        await page.goto(
            f"{MY_URL}?page_navi=11&page_size=1000&sort_key=start_date",
            wait_until="networkidle",
        )
        checkboxes = await page.query_selector_all('input[name="chkbox[]"]')

        listings = []
        for chk in checkboxes:
            lid  = await chk.get_attribute("value")
            link = await page.query_selector(f'a[href*="write.php"][href*="id={lid}"]')
            if not link:
                logger.debug(f"수정 링크 없음, 건너뜀: ID={lid}")
                continue
            href = await link.get_attribute("href")
            row_el = await chk.evaluate_handle('el => el.closest("tr")')
            row  = await row_el.evaluate('el => el ? el.innerText : ""')
            row  = " ".join(row.split())
            dates = re.findall(r'\d{2}\.\d{2}', row)
            start_date = dates[-1] if dates else "99.99"
            # 매물 유형 코드 (ID 앞 2자리: 11=아파트, 21=오피스텔, 31=빌라, 51=상가 등)
            code = int(lid[:2]) if lid and len(lid) >= 2 and lid[:2].isdigit() else 0
            # 거래 유형 (행 텍스트에서 추출)
            b_type = "기타"
            for t in ["매매", "전세", "월세", "단기"]:
                if t in row:
                    b_type = t
                    break
            listings.append({"id": lid, "href": href, "start_date": start_date, "code": code, "b_type": b_type, "row_text": row})

        # 직접 최초등록일 기준 오름차순 정렬
        listings.sort(key=lambda x: x["start_date"])
        return listings

    # ──────────────────────────────────────────────────────────────────────
    # 기존 매물 데이터 추출 (수정 페이지)
    # ──────────────────────────────────────────────────────────────────────
    async def _extract_data(self, old_id: str) -> dict:
        page = self._page
        await page.goto(
            f"{MY_URL}?page_navi=11&page_size=1000&sort_key=start_date",
            wait_until="networkidle",
        )
        edit_link = await page.query_selector(
            f'a[href*="write.php"][href*="id={old_id}"]'
        )
        if not edit_link:
            raise RuntimeError(f"수정 링크 없음: {old_id}")
        await edit_link.click()
        await page.wait_for_load_state("networkidle")

        data = await page.evaluate("""() => {
            const f = document.form;
            const get = name => {
                const el = f.elements[name];
                if (!el) return '';
                if (el.type === 'radio' || el.type === 'checkbox') {
                    // NodeList 인 경우
                    const checked = [];
                    (el.length !== undefined ? Array.from(el) : [el])
                        .filter(e => e.checked).forEach(e => checked.push(e.value));
                    return checked.length === 1 ? checked[0] : checked;
                }
                return el.value || '';
            };
            const bidSel = f.bid;
            const bidName = bidSel && bidSel.selectedIndex >= 0
                ? bidSel.options[bidSel.selectedIndex].text : '';
            const sizeSel = f.size_type;
            const sizeOpts = sizeSel
                ? Array.from(sizeSel.options).map(o => ({val: o.value, txt: o.text})) : [];
            return {
                code:         get('code'),
                open_svr:     get('open_svr'),
                open_term:    get('open_term'),
                wr_plus_mm:   get('wr_plus_mm'),
                wr_net:       get('wr_net'),
                wr_gcom:      get('wr_gcom'),
                wr_gnet:      get('wr_gnet'),
                build_type:   get('build_type'),
                sido:         get('sido'),
                gugun:        get('gugun'),
                dong:         get('dong'),
                ri:           get('ri'),
                bid:          get('bid'),
                bid_name:     bidName,
                bname:        get('bname'),
                lot0:         get('lot0'),
                lot1:         get('lot1'),
                lot2:         get('lot2'),
                dongsu:       get('dongsu'),
                hosu:         get('hosu'),
                open_add:     get('open_add'),
                smap:         get('smap'),
                area_id:      get('area_id'),
                b_type:       get('b_type'),
                p_mode:       get('p_mode'),
                sprice:       get('sprice'),
                yprice:       get('yprice'),
                dprice:       get('dprice'),
                rprice:       get('rprice'),
                cprice:       get('cprice'),
                lone:         get('lone'),
                dprofit:      get('dprofit'),
                rprofit:      get('rprofit'),
                mprice:       get('mprice'),
                fprice:       get('fprice'),
                wr_bosu:      get('wr_bosu'),
                commi_detail: get('commi_detail'),
                size_type:    get('size_type'),
                size_opts:    sizeOpts,
                sale_size:    get('sale_size'),
                use_size:     get('use_size'),
                used:         get('used'),
                room:         get('room'),
                bathroom:     get('bathroom'),
                room_struc:   get('room_struc'),
                floor:        get('floor'),
                t_floor:      get('t_floor'),
                floor_pre:    get('floor_pre'),
                direction:    get('direction'),
                entrance:     get('entrance'),
                permit_day1:  get('permit_day1'),
                permit_day2:  get('permit_day2'),
                park:         get('park'),
                vertical:     get('vertical'),
                power:        get('power'),
                heat:         get('heat'),
                fuel:         get('fuel'),
                move_in:      get('move_in'),
                move_day:     get('move_day'),
                equipment:    get('equipment'),
                subway:       get('subway'),
                station:      get('station'),
                subway_space: get('subway_space'),
                title:        get('title'),
                content:      get('content'),
                wr_content2:  get('wr_content2'),
                secret:       get('secret'),
                img_name_0:   get('img_name_0'),
                img_name_1:   get('img_name_1'),
                img_name_2:   get('img_name_2'),
                img_name_3:   get('img_name_3'),
                img_name_4:   get('img_name_4'),
                wr_wm_content:   get('wr_wm_content'),
                wr_wm_size:      get('wr_wm_size'),
                wr_wm_position:  get('wr_wm_position'),
                phone11: get('phone11'),
                phone12: get('phone12'),
                phone13: get('phone13'),
                phone21: get('phone21'),
                phone22: get('phone22'),
                phone23: get('phone23'),
            };
        }""")
        logger.debug(f"데이터 추출 완료: code={data['code']}, gugun={data['gugun']}, dong={data['dong']}")
        return data

    # ──────────────────────────────────────────────────────────────────────
    # 신규 폼 입력
    # ──────────────────────────────────────────────────────────────────────
    async def _fill_form(self, data: dict):
        page = self._page
        code = int(data["code"])

        # 신규 등록 폼으로 이동
        await page.goto(
            f"{MY_URL}write.php?page_navi=11&code={code}&list_url=../my/",
            wait_until="networkidle",
        )

        async def safe_select(name: str, value: str):
            if not value:
                return
            try:
                await page.select_option(f'[name={name}]', value)
            except Exception:
                pass

        async def safe_fill(name: str, value: str):
            if not value:
                return
            try:
                await page.fill(f'[name={name}]', str(value))
            except Exception:
                pass

        async def safe_radio(name: str, value: str):
            if not value:
                return
            try:
                await page.check(f'[name={name}][value="{value}"]')
            except Exception:
                pass

        # ── 광고 설정 ──────────────────────────────────────────────────
        await safe_radio("open_svr",   data["open_svr"])
        await safe_radio("wr_plus_mm", data["wr_plus_mm"])
        await safe_radio("open_term",  data["open_term"])

        # ── 주소: gugun → dong cascade ─────────────────────────────────
        await safe_select("gugun", data["gugun"])
        await page.evaluate("loadAddr('dong')")
        try:
            await page.wait_for_function(
                "document.querySelector('[name=dong]').options.length > 1",
                timeout=8000,
            )
        except Exception:
            logger.warning("dong 옵션 로드 타임아웃")

        await safe_select("dong", data["dong"])
        await page.evaluate("loadAddr('ri')")
        await asyncio.sleep(0.8)

        if data.get("ri"):
            await safe_select("ri", data["ri"])

        # ── bid (단지) 또는 번지 ───────────────────────────────────────
        if code in NEEDS_BID:
            bid_name = data.get("bid_name") or data.get("bname", "")
            if bid_name:
                await safe_fill("s_bname", bid_name)
                await page.evaluate("loadAddr('bid')")
                try:
                    await page.wait_for_function(
                        f"Array.from(document.querySelector('[name=bid]').options)"
                        f".some(o => o.value === '{data['bid']}')",
                        timeout=8000,
                    )
                except Exception:
                    logger.warning("bid 옵션 로드 타임아웃")
                await safe_select("bid", data["bid"])
                await asyncio.sleep(0.5)  # bid onchange → size_type 로드 대기

                # size_type (면적 유형)
                if data.get("size_type"):
                    try:
                        await page.wait_for_function(
                            f"document.querySelector('[name=size_type]').options.length > 1",
                            timeout=5000,
                        )
                    except Exception:
                        pass
                    await safe_select("size_type", data["size_type"])

            if data.get("dongsu"):
                await safe_fill("dongsu", data["dongsu"])
            if data.get("hosu"):
                await safe_fill("hosu",   data["hosu"])
        else:
            # 상가, 사무실 등: 번지수 직접 입력
            if data.get("lot1"):
                await safe_fill("lot1", data["lot1"])
            if data.get("lot2"):
                await safe_fill("lot2", data["lot2"])
            if data.get("bname"):
                await safe_fill("bname", data["bname"])
            if data.get("hosu"):
                await safe_fill("hosu",  data["hosu"])

        # 주소공개
        await safe_radio("open_add", data["open_add"])

        # 지도 (smap 직접 주입)
        if data.get("smap"):
            await page.evaluate(f"if(document.form.smap) document.form.smap.value='{data['smap']}'")
        if data.get("area_id"):
            await page.evaluate(f"if(document.form.area_id) document.form.area_id.value='{data['area_id']}'")

        # ── 거래 유형 & 가격 ──────────────────────────────────────────
        await safe_radio("b_type", data["b_type"])
        await safe_select("p_mode", data["p_mode"])
        for f in ["sprice","yprice","dprice","rprice","cprice","lone","dprofit","rprofit","mprice","fprice"]:
            await safe_fill(f, data.get(f, ""))
        await safe_select("wr_bosu",      data["wr_bosu"])
        await safe_fill("commi_detail",   data["commi_detail"])

        # ── 면적 ──────────────────────────────────────────────────────
        await safe_fill("sale_size", data["sale_size"])
        await safe_fill("use_size",  data["use_size"])

        # ── 상세 정보 ─────────────────────────────────────────────────
        if data.get("used"):
            try:
                await page.check(f'[name=used][value="{data["used"]}"]')
            except Exception:
                await safe_fill("used", data["used"])
        if data.get("room"):
            await safe_fill("room",     data["room"])
        if data.get("bathroom"):
            await safe_fill("bathroom", data["bathroom"])
        await safe_select("room_struc",  data["room_struc"])
        await safe_select("floor_pre",   data["floor_pre"])
        await safe_fill("floor",         data["floor"])
        await safe_fill("t_floor",       data["t_floor"])
        await safe_select("direction",   data["direction"])
        await safe_select("entrance",    data["entrance"])
        await safe_select("permit_day1", data["permit_day1"])
        await safe_select("permit_day2", data["permit_day2"])
        await safe_fill("park",          data["park"])
        await safe_fill("vertical",      data["vertical"])
        await safe_fill("power",         data["power"])
        await safe_select("heat",        data["heat"])
        await safe_select("fuel",        data["fuel"])

        # ── 입주 & 교통 ──────────────────────────────────────────────
        await safe_radio("move_in", data["move_in"])
        await safe_fill("move_day",  data["move_day"])
        await safe_fill("equipment", data["equipment"])
        await safe_select("subway",       data["subway"])
        await safe_select("station",      data["station"])
        await safe_select("subway_space", data["subway_space"])

        # ── 설명 & 메모 ───────────────────────────────────────────────
        await safe_fill("title",       data["title"])
        await safe_fill("content",     data["content"])
        await safe_fill("wr_content2", data["wr_content2"])
        await safe_fill("secret",      data["secret"])

        # ── 워터마크 ──────────────────────────────────────────────────
        await safe_fill("wr_wm_content",   data["wr_wm_content"])
        await safe_select("wr_wm_size",    data["wr_wm_size"])
        await safe_select("wr_wm_position",data["wr_wm_position"])

        logger.debug("폼 입력 완료")

    # ──────────────────────────────────────────────────────────────────────
    # 재등록 후 검증
    # ──────────────────────────────────────────────────────────────────────
    async def _verify_relist(self, old_id: str, old_sig: str = None, expected_total: int = None) -> bool:
        """① 총 매물 수 불변  ② 기존 ID 삭제  ③ 동일 매물 존재 확인"""
        page = self._page
        listings_url = f"{MY_URL}?page_navi=11&page_size=1000&sort_key=start_date"
        try:
            await page.goto(listings_url, wait_until="networkidle")
        except Exception:
            await asyncio.sleep(2)
            await page.goto(listings_url, wait_until="networkidle")
        checkboxes = await page.query_selector_all('input[name="chkbox[]"]')
        post_total = len(checkboxes)
        ok = True

        # ① 총 매물 수
        if expected_total is not None:
            if post_total == expected_total:
                logger.info(f"[검증 ✔] 총 매물 수 유지: {post_total}개")
            else:
                logger.error(f"[검증 ✘] 총 매물 수 불일치: 전 {expected_total}개 → 후 {post_total}개")
                ok = False

        # ② 기존 ID 삭제 확인
        old_el = await page.query_selector(f'input[name="chkbox[]"][value="{old_id}"]')
        if old_el:
            logger.error(f"[검증 ✘] 기존 ID={old_id} 아직 존재 (삭제 실패)")
            ok = False
        else:
            logger.info(f"[검증 ✔] 기존 ID={old_id} 삭제 확인")

        # ③ 동일 매물 확인 (서명 비교)
        if old_sig:
            new_id = None
            for chk in checkboxes:
                lid = await chk.get_attribute("value")
                if lid == old_id:
                    continue
                row_el = await chk.evaluate_handle('el => el.closest("tr")')
                row_text = await row_el.evaluate('el => el ? el.innerText : ""')
                row_text = ' '.join(row_text.split())
                if GongsilManager._row_sig(row_text) == old_sig:
                    new_id = lid
                    break
            if new_id:
                logger.info(f"[검증 ✔] 신규 ID={new_id}로 동일 매물 확인")
            else:
                logger.warning(f"[검증 △] 동일 매물 서명 불일치 (원본 ID={old_id}) — 수동 확인 필요")

        return ok

    # ──────────────────────────────────────────────────────────────────────
    # 매물 1개 재등록
    # ──────────────────────────────────────────────────────────────────────
    async def _relist_one(self, old_id: str, old_sig: str = None, expected_total: int = None) -> bool:
        page = self._page
        logger.info(f"재등록 시작 → ID={old_id}")

        # 1. 기존 매물 데이터 추출
        try:
            data = await self._extract_data(old_id)
        except Exception as e:
            logger.error(f"데이터 추출 실패: {e}")
            return False

        # 2. 신규 폼 이동 및 입력
        try:
            await self._fill_form(data)
        except Exception as e:
            logger.error(f"폼 입력 실패: {e}")
            return False

        # 3. 폼 제출
        try:
            async with page.expect_response(
                lambda r: "write_update.php" in r.url, timeout=20000
            ) as resp_info:
                await page.evaluate("""() => {
                    var r = farticle_submit(document.form);
                    if (r !== false) document.form.submit();
                }""")
            resp = await resp_info.value
            if resp.status != 200:
                logger.error(f"신규 등록 실패: HTTP {resp.status}")
                return False
            logger.info(f"신규 등록 완료 (HTTP {resp.status})")
        except Exception as e:
            logger.error(f"폼 제출 오류: {e}")
            return False

        # 4. 목록으로 돌아와서 기존 매물 삭제
        await page.goto(
            f"{MY_URL}?page_navi=11&page_size=1000&sort_key=start_date",
            wait_until="networkidle",
        )
        try:
            async with page.expect_response(
                lambda r: "delete.php" in r.url, timeout=15000
            ) as del_info:
                await page.evaluate(f"""() => {{
                    var frm = document.chkform;
                    frm.id.value         = '{old_id}';
                    frm.update_key.value = 'at_delete';
                    frm.method           = 'post';
                    frm.action           = 'delete.php';
                    frm.target           = 'gongsil_tmp_frame';
                    frm.submit();
                }}""")
            del_resp = await del_info.value
            logger.info(f"기존 매물 삭제: ID={old_id} (HTTP {del_resp.status})")
        except Exception as e:
            logger.error(f"기존 매물 삭제 실패 - 수동 삭제 필요: ID={old_id}, 오류: {e}")
            return False

        # 5. 재등록 검증 (오류가 발생해도 재등록 자체는 완료된 것으로 처리)
        try:
            await asyncio.sleep(1)   # 삭제 후 브라우저 네비게이션 정리 대기
            await self._verify_relist(old_id, old_sig, expected_total)
        except Exception as e:
            logger.warning(f"검증 중 오류 (재등록은 완료): {e}")

        return True

    # ──────────────────────────────────────────────────────────────────────
    # 메인 진입점
    # ──────────────────────────────────────────────────────────────────────
    async def refresh_all_listings(self, group: str = "all"):
        listings = await self._load_listings()

        if group == "frequent":
            listings = [l for l in listings if _is_frequent(l)]
        elif group == "weekly":
            listings = [l for l in listings if not _is_frequent(l)]

        total = len(listings)

        if total == 0:
            logger.info(f"[{group}] 처리할 매물이 없습니다.")
            return

        # max_per_run=None이면 하루 5회 균등 분배 자동 계산
        if self.max_per_run is None:
            count = get_daily_per_run(total)
        else:
            count = self.max_per_run

        to_process = listings[:count]

        # 실행 전: 처리 대상 목록 기록
        logger.info(f"[{group}] ▶ 실행 전 총 {total}개 | 이번 처리 {len(to_process)}개:")
        for i, lst in enumerate(to_process):
            sig = GongsilManager._row_sig(lst.get("row_text", ""))
            logger.info(f"  [{i+1}] ID={lst['id']} 거래={lst['b_type']} 등록일={lst['start_date']} | {sig}")

        success = 0
        for i, lst in enumerate(to_process):
            logger.info(
                f"[{i+1}/{len(to_process)}] ID={lst['id']} "
                f"코드={lst['code']} 거래={lst['b_type']} 최초등록일={lst['start_date']}"
            )
            old_sig = GongsilManager._row_sig(lst["row_text"]) if lst.get("row_text") else None
            ok = await self._relist_one(lst["id"], old_sig=old_sig, expected_total=total)
            if ok:
                success += 1
            if i < len(to_process) - 1:
                await asyncio.sleep(2)

        logger.info(f"[{group}] ◀ 완료: {success}/{len(to_process)}개 성공 (총 매물 수 목표: {total}개)")
