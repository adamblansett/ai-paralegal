from fastapi import APIRouter

from app.api.v1 import analyses, audit, documents, exports, findings, health, matters

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(matters.router)
api_v1_router.include_router(documents.router)
api_v1_router.include_router(analyses.router)
api_v1_router.include_router(findings.router)
api_v1_router.include_router(audit.router)
api_v1_router.include_router(exports.router)
