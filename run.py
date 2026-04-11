"""
공실닷컴 매물 자동 갱신 스케줄러

스케줄:
  [자주] 아파트/오피스텔 전세·월세·단기 → 매일 09,11,13,15,17시 각 5개
  [주간] 상가/주택/매매 등 나머지        → 매주 지정 요일 1회

실행 방법:
  python run.py                      # 스케줄 자동 실행
  python run.py --now                # 전체 1회 즉시 실행
  python run.py --now --frequent     # 자주 그룹 1회 즉시 실행
  python run.py --now --weekly       # 주간 그룹 1회 즉시 실행
  python run.py --now --show         # 브라우저 화면 표시
"""
import asyncio, os, sys
from datetime import datetime
from pathlib import Path

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
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

GROUP_LABEL = {
    "frequent": "아파트/오피스텔 전세·월세·단기",
    "weekly":   "상가/주택/매매",
    "all":      "전체",
}

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
        # 자주 실행 (아파트/오피스텔 전세·월세·단기)
        "frequent_times": [t.strip() for t in os.getenv("FREQUENT_TIMES", "09:00,11:00,13:00,15:00,17:00").split(",")],
        "frequent_max":   int(os.getenv("FREQUENT_MAX", "5")) or None,
        # 주간 실행 (상가/주택/매매 등)
        "weekly_day":     os.getenv("WEEKLY_DAY", "mon").strip().lower(),
        "weekly_time":    os.getenv("WEEKLY_TIME", "09:00").strip(),
        "weekly_max":     int(os.getenv("WEEKLY_MAX", "0")) or None,
    }

async def run_refresh(cfg: dict, group: str = "all"):
    now   = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    label = GROUP_LABEL.get(group, group)
    logger.info(f"{'='*50}\n매물 갱신 시작 [{label}]: {now} KST\n{'='*50}")

    max_run = cfg["frequent_max"] if group == "frequent" else cfg["weekly_max"]
    async with GongsilManager(
        cfg["username"], cfg["password"], cfg["page_id"],
        cfg["headless"], max_run
    ) as m:
        await m.refresh_all_listings(group=group)

    logger.info(f"{'='*50}\n매물 갱신 완료 [{label}]\n{'='*50}")

async def main():
    cfg = _load_config()
    if "--show" in sys.argv:
        cfg["headless"] = False

    if "--now" in sys.argv:
        if "--frequent" in sys.argv:
            await run_refresh(cfg, group="frequent")
        elif "--weekly" in sys.argv:
            await run_refresh(cfg, group="weekly")
        else:
            await run_refresh(cfg, group="all")
        return

    scheduler = AsyncIOScheduler(timezone=KST)

    # ── 자주 실행: 매일 09,11,13,15,17시 (아파트/오피스텔 전세·월세·단기) ──
    for t in cfg["frequent_times"]:
        try:
            h, m = map(int, t.split(":"))
            scheduler.add_job(
                run_refresh,
                CronTrigger(hour=h, minute=m, timezone=KST),
                args=[cfg, "frequent"],
                id=f"frequent_{t}",
                misfire_grace_time=300,
                replace_existing=True,
            )
            logger.info(f"자주 스케줄 등록: 매일 {t} KST [{GROUP_LABEL['frequent']}]")
        except ValueError:
            logger.warning(f"잘못된 시간 형식 무시: {t}")

    # ── 주간 실행: 매주 지정 요일 (상가/주택/매매) ──
    try:
        wh, wm = map(int, cfg["weekly_time"].split(":"))
        scheduler.add_job(
            run_refresh,
            CronTrigger(day_of_week=cfg["weekly_day"], hour=wh, minute=wm, timezone=KST),
            args=[cfg, "weekly"],
            id="weekly",
            misfire_grace_time=300,
            replace_existing=True,
        )
        logger.info(
            f"주간 스케줄 등록: 매주 {cfg['weekly_day']} {cfg['weekly_time']} KST "
            f"[{GROUP_LABEL['weekly']}]"
        )
    except ValueError:
        logger.warning(f"주간 스케줄 시간 형식 오류: {cfg['weekly_time']}")

    scheduler.start()
    logger.info("스케줄러 실행 중. 종료: Ctrl+C")
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
