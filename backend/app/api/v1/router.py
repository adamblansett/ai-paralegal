from fastapi import APIRouter

from app.api.v1 import (
    analyses,
    audit,
    auth,
    documents,
    exports,
    findings,
    health,
    matters,
    playbooks,
)

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(playbooks.router)
api_v1_router.include_router(matters.router)
api_v1_router.include_router(documents.router)
api_v1_router.include_router(analyses.router)
api_v1_router.include_router(findings.router)
api_v1_router.include_router(audit.router)
api_v1_router.include_router(exports.router)
