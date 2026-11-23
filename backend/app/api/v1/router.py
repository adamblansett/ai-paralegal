from fastapi import APIRouter

from app.api.v1 import (
    admin,
    analyses,
    audit,
    auth,
    documents,
    events,
    exports,
    findings,
    health,
    integrations,
    matters,
    metrics,
    playbooks,
)

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(admin.router)
api_v1_router.include_router(playbooks.router)
api_v1_router.include_router(matters.router)
api_v1_router.include_router(documents.router)
api_v1_router.include_router(analyses.router)
api_v1_router.include_router(findings.router)
api_v1_router.include_router(audit.router)
api_v1_router.include_router(exports.router)
api_v1_router.include_router(events.router)
api_v1_router.include_router(integrations.router)
api_v1_router.include_router(metrics.router)
