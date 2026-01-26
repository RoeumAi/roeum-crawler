#!/bin/bash

# Prefect 워커 풀 및 서버 설정 스크립트

echo "======================================================================"
echo "🚀 Prefect 워커 풀 설정"
echo "======================================================================"

# 1. Prefect 서버 상태 확인
echo ""
echo "📍 Step 1: Prefect 서버 상태 확인..."
prefect server inspect 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✅ Prefect 서버가 실행 중입니다"
else
    echo "⚠️  Prefect 서버가 실행 중이 아닙니다. 시작합니다..."
    prefect server start &
    SERVER_PID=$!
    sleep 3
fi

# 2. 기존 work pool 확인
echo ""
echo "📍 Step 2: 기존 work pool 확인..."
POOL_EXISTS=$(prefect work-pool ls | grep -c "default")

if [ $POOL_EXISTS -gt 0 ]; then
    echo "✅ 'default' work pool이 이미 존재합니다"
else
    echo "🔧 'default' work pool 생성 중..."
    prefect work-pool create --type process default
    if [ $? -eq 0 ]; then
        echo "✅ Work pool 'default' 생성 완료"
    else
        echo "❌ Work pool 생성 실패"
        exit 1
    fi
fi

# 3. 워커 시작 안내
echo ""
echo "======================================================================"
echo "✅ Prefect 워커 풀 설정 완료!"
echo "======================================================================"
echo ""
echo "📌 워커 시작 명령어:"
echo "   prefect worker start --pool default"
echo ""
echo "📌 또는 백그라운드에서 실행:"
echo "   prefect worker start --pool default &"
echo ""
echo "======================================================================"
