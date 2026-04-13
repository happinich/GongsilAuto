"""
공실닷컴 매물 자동 갱신

스케줄: 매일 09,11,13,15,17시 — 전체 매물을 5회에 균등 분배
  (아침 첫 실행 시 총 매물 수 ÷ 5 = 1회 처리 수 자동 계산)

실행 방법:
  python run.py --now         # 즉시 1회 실행
  python run.py --now --show  # 브라우저 화면 표시
"""
import asyncio, os, sys, urllib.request, urllib.parse, json
from datetime import datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv
from loguru import logger
from gongsil import GongsilManager

load_dotenv()
Path("logs").mkdir(exist_ok=True)
logger.add(
    "logs/gongsil_{time:YYYY-MM-DD}.log",
    rotation="1 day", retention="30 days",
    level="INFO", encoding="utf-8",
    format="{time:HH:mm:ss} | {level} | {message}",
)
KST = pytz.timezone("Asia/Seoul")

def send_telegram(message: str):
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
        req  = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.warning(f"텔레그램 알림 실패: {e}")

def _load_config():
    username = os.getenv("GONGSIL_ID", "").strip()
    password = os.getenv("GONGSIL_PW", "").strip()
    if not username or not password:
        print(".env 파일에 GONGSIL_ID 와 GONGSIL_PW 를 입력하세요.")
        sys.exit(1)
    return {
        "username": username,
        "password": password,
        "page_id":  os.getenv("GONGSIL_PAGE", "").strip(),
        "headless": os.getenv("HEADLESS", "true").lower() != "false",
    }

async def run_refresh(cfg: dict):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"{'='*50}\n매물 갱신 시작: {now} KST\n{'='*50}")

    success = 0
    total_processed = 0
    per_run = 0

    try:
        async with GongsilManager(
            cfg["username"], cfg["password"], cfg["page_id"],
            cfg["headless"], max_per_run=None
        ) as m:
            # 결과 집계를 위해 refresh_all_listings 결과 캡처
            from gongsil import get_daily_per_run
            listings = await m._load_listings()
            total_listings = len(listings)
            per_run = get_daily_per_run(total_listings)
            to_process = listings[:per_run]
            total_processed = len(to_process)

            for i, lst in enumerate(to_process):
                logger.info(
                    f"[{i+1}/{total_processed}] ID={lst['id']} "
                    f"코드={lst['code']} 거래={lst['b_type']} 최초등록일={lst['start_date']}"
                )
                ok = await m._relist_one(lst["id"])
                if ok:
                    success += 1
                if i < total_processed - 1:
                    await asyncio.sleep(2)

        logger.info(f"[all] 완료: {success}/{total_processed}개 성공")
        time_label = datetime.now(KST).strftime("%H:%M")
        send_telegram(
            f"✅ 공실닷컴 갱신 완료 [{time_label}]\n"
            f"전체 {total_listings}개 중 {success}/{total_processed}개 성공"
        )
    except Exception as e:
        logger.error(f"갱신 중 오류: {e}")
        send_telegram(f"❌ 공실닷컴 갱신 오류\n{e}")
        raise

    logger.info(f"{'='*50}\n매물 갱신 완료\n{'='*50}")

async def main():
    cfg = _load_config()
    if "--show" in sys.argv:
        cfg["headless"] = False
    await run_refresh(cfg)

if __name__ == "__main__":
    import traceback
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
