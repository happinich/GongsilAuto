"""
공실닷컴 매물 자동 갱신 스케줄러
  python run.py          # 매일 자동 실행
  python run.py --now    # 즉시 1회 실행
  python run.py --now --show  # 브라우저 화면 보면서 실행
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
logger.add("logs/gongsil_{time:YYYY-MM-DD}.log", rotation="1 day", retention="30 days", level="INFO", encoding="utf-8", format="{time:HH:mm:ss} | {level} | {message}")
KST = pytz.timezone("Asia/Seoul")

def _load_config():
    username = os.getenv("GONGSIL_ID", "").strip()
    password = os.getenv("GONGSIL_PW", "").strip()
    if not username or not password:
        print(".env 파일에 GONGSIL_ID 와 GONGSIL_PW 를 입력하세요.")
        sys.exit(1)
    return {
        "username": username, "password": password,
        "page_id": os.getenv("GONGSIL_PAGE", "").strip(),
        "headless": os.getenv("HEADLESS", "true").lower() != "false",
        "max_per_run": int(os.getenv("MAX_PER_RUN", "0")) or None,
        "schedule_times": [t.strip() for t in os.getenv("SCHEDULE_TIMES", "09:00,18:00").split(",")],
    }

async def run_refresh(cfg):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"{'='*50}\n매물 갱신 시작: {now} KST\n{'='*50}")
    async with GongsilManager(cfg["username"], cfg["password"], cfg["page_id"], cfg["headless"], cfg["max_per_run"]) as m:
        await m.refresh_all_listings()
    logger.info(f"{'='*50}\n매물 갱신 완료\n{'='*50}")

async def main():
    cfg = _load_config()
    if "--show" in sys.argv: cfg["headless"] = False
    if "--now" in sys.argv:
        await run_refresh(cfg)
        return
    scheduler = AsyncIOScheduler(timezone=KST)
    for t in cfg["schedule_times"]:
        try:
            h, m = map(int, t.split(":"))
            scheduler.add_job(run_refresh, CronTrigger(hour=h, minute=m, timezone=KST), args=[cfg], id=f"refresh_{t}", misfire_grace_time=300, replace_existing=True)
            logger.info(f"스케줄 등록: 매일 {t} KST")
        except ValueError: pass
    scheduler.start()
    logger.info("스케줄러 실행 중. 종료: Ctrl+C")
    try:
        while True: await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
