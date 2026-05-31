#!/bin/bash
# GCP VM 초기 세팅 스크립트
# SSH 접속 후 이 파일을 복사해서 실행하세요

echo "=== 1. 시스템 업데이트 ==="
sudo apt update && sudo apt upgrade -y

echo "=== 2. Python 설치 ==="
sudo apt install -y python3 python3-pip python3-venv

echo "=== 3. 프로젝트 폴더 생성 ==="
mkdir -p ~/bitcoin_trader
cd ~/bitcoin_trader

echo "=== 4. 가상환경 생성 ==="
python3 -m venv venv
source venv/bin/activate

echo "=== 5. 패키지 설치 ==="
pip install pyupbit pandas schedule python-dotenv requests

echo "=== 6. .env 파일 생성 ==="
if [ ! -f .env ]; then
    echo "UPBIT_ACCESS_KEY=여기에_ACCESS_KEY_입력" > .env
    echo "UPBIT_SECRET_KEY=여기에_SECRET_KEY_입력" >> .env
    echo ">>> .env 파일이 생성되었습니다. nano .env 로 키를 입력하세요."
fi

echo ""
echo "=== 세팅 완료! ==="
echo "다음 단계:"
echo "  1. nano .env  → API 키 입력"
echo "  2. 각 .py 파일 업로드"
echo "  3. python3 server_bot.py 로 테스트"
echo "  4. 백그라운드 실행: nohup python3 server_bot.py > bot.log 2>&1 &"
