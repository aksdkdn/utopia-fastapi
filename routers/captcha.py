from fastapi import APIRouter, Form, UploadFile, File
import httpx
import uuid
import random
import json
import redis.asyncio as redis # 🌟 비동기 Redis 추가

from core.config import settings

router = APIRouter()

# 🌟 1. 설정 파일의 주소로 Redis 클라이언트 연결
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

GPU_SERVER_URL = settings.GPU_SERVER_URL
BASE_POSES = ["주먹 ✊", "손바닥 🖐️", "브이 ✌️", "따봉 👍"]
FINGER_POSES = [f"손가락 {i}개 펼치기 🤚" for i in range(1, 6)]
ALL_POSES = BASE_POSES + FINGER_POSES
MAX_ATTEMPTS = 5  # 🌟 최대 실패 허용 횟수

@router.post("/handocr/start")
async def start_captcha():
    chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    random_text = ''.join(random.choice(chars) for _ in range(5))
    random_pose = random.choice(ALL_POSES)
    
    session_id = str(uuid.uuid4())
    
    # 🌟 2. Redis에 저장할 데이터 묶기
    session_data = {
        "text": random_text, 
        "pose": random_pose, 
        "attempts": 0
    }
    
    # 🌟 3. Redis에 저장! (setex를 쓰면 300초(5분) 뒤에 알아서 깔끔하게 삭제됩니다)
    await redis_client.setex(
        f"captcha:{session_id}", 
        300, 
        json.dumps(session_data)
    )
    
    return {"sessionId": session_id, "text": random_text, "pose": random_pose}

@router.post("/handocr/verify")
async def verify_captcha(sessionId: str = Form(...), image: UploadFile = File(...)):
    
    # 1. Redis에서 세션 정보 꺼내오기
    session_str = await redis_client.get(f"captcha:{sessionId}")
    if not session_str:
        return {"success": False, "message": "유효하지 않거나 5분이 지나 만료된 세션입니다. 새로고침 해주세요."}
    
    session_data = json.loads(session_str)
    expected_pose = session_data["pose"]
    expected_text = session_data["text"] # (OCR 텍스트 정답도 꺼내둡니다)
    
    # 🌟 2. Soft Ban (실패 횟수 초과 확인)
    if session_data.get("attempts", 0) >= MAX_ATTEMPTS:
        await redis_client.delete(f"captcha:{sessionId}") # 세션 완전 파기
        return {"success": False, "message": "실패 횟수(5회)를 초과했습니다. 새로고침하여 처음부터 다시 시도해주세요."}
    
    image_bytes = await image.read()
    
    # 3. GPU 서버와 통신
    async with httpx.AsyncClient() as client:
        files = {'image': (image.filename, image_bytes, image.content_type)}
        try:
            response = await client.post(GPU_SERVER_URL, files=files, timeout=10.0)
            response.raise_for_status()
            gpu_result = response.json()
        except Exception as e:
            return {"success": False, "message": f"AI 서버 통신 오류: {str(e)}"}

    if not gpu_result.get("success"):
        return {"success": False, "message": gpu_result.get("message", "손 포즈를 판독하지 못했습니다.")}
        
    detected_pose = gpu_result.get("detected_pose")
    # detected_text = gpu_result.get("detected_text") # 🌟 추후 OCR 모델이 추가되면 활성화!
    
    # 4. 정답 대조 및 실패 처리
    if detected_pose != expected_pose:
        session_data["attempts"] += 1
        await redis_client.setex(f"captcha:{sessionId}", 300, json.dumps(session_data))
        
        return {
            "success": False, 
            "message": f"손 포즈가 틀렸습니다.\n(요구: {expected_pose} / 인식됨: {detected_pose})\n남은 기회: {MAX_ATTEMPTS - session_data['attempts']}회"
        }
        
    # 🌟 5. 인증 성공 시: 합격 토큰(Pass Token) 발급!
    pass_token = str(uuid.uuid4())
    
    # 합격 토큰을 Redis에 3분(180초) 동안만 유효하게 저장합니다. (프론트엔드가 이 시간 안에 회원가입을 완료해야 함)
    await redis_client.setex(f"captcha_pass:{pass_token}", 180, "PASSED")
    
    # 기존 문제 세션은 지워줍니다.
    await redis_client.delete(f"captcha:{sessionId}")
    
    return {
        "success": True, 
        "message": "인증이 완료되었습니다.",
        "passToken": pass_token  # 🌟 프론트엔드에게 합격 목걸이를 건네줍니다.
    }