# app/utils/normalize.py
import os

def normalize_hash(h: str) -> str:
    """
    Commit SHA 정규화: None/빈값 방어, strip, lowercase
    """
    if not h:
        return ""
    return h.strip().lower()

def normalize_path(p: str) -> str:
    """
    파일 경로 정규화:
    - strip
    - 역슬래시를 슬래시로 변환
    - os.path.normpath로 중복/상대표현 정리
    - '.' 결과는 빈 문자열로 처리
    """
    if not p:
        return ""
    p = p.strip()
    p = p.replace("\\", "/")
    p = os.path.normpath(p).replace("\\", "/")
    if p == ".":
        return ""
    return p
