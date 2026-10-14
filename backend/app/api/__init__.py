from fastapi import APIRouter

from app.api import (analysis, audit, auth, dashboard, metrics, notices,
                     perf, rules, rule_suggest, sse, tasks, teams, users)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(teams.router)
api_router.include_router(notices.router)
api_router.include_router(users.router)
api_router.include_router(audit.router)
api_router.include_router(tasks.router)
api_router.include_router(analysis.router)
api_router.include_router(sse.router)
api_router.include_router(rules.router)
api_router.include_router(rules.team_router)
api_router.include_router(rule_suggest.router)
api_router.include_router(metrics.router)
api_router.include_router(perf.router)
