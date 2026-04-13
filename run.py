"""
공실닷컴 매물 자동 갱신

스케줄: 매일 09,11,13,15,17시 — 전체 매물을 5회에 균등 분배
  (아침 첫 실행 시 총 매물 수 ÷ 5 = 1회 처리 수 자동 계산)

실행 방법:
  python run.py --now         # 즉시 1회 실행
  python run.py --now --show  # 브라우저 화면 표시
"""
import asyncio, os, sys
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
    # max_per_run=None → get_daily_per_run()으로 자동 계산
    async with GongsilManager(
        cfg["username"], cfg["password"], cfg["page_id"],
        cfg["headless"], max_per_run=None
    ) as m:
        await m.refresh_all_listings(group="all")
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
