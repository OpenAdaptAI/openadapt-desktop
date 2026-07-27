"""The explicit seam between Desktop's portal and Flow's attended console.

Desktop owns transport; ``openadapt-flow`` owns every decision semantic.  This
module is the *only* place Desktop talks to the attended console, and it does
exactly four things:

1.  Holds the loopback base URL and the console's bearer/CSRF capability.
2.  Enforces a **closed route allowlist** (:data:`FLOW_ROUTES`).  A portal
    request that does not map to one of these upstream routes is refused before
    any network call, so the phone can never reach an arbitrary console route.
3.  Normalizes ``Host`` and ``Origin`` to loopback.  Flow's console middleware
    refuses any request whose ``Host`` is not ``127.0.0.1``/``localhost`` and
    any ``Origin`` that is not the matching ``http://127.0.0.1:<port>``.  A
    phone's request arrives with the customer ingress hostname, so without this
    normalization every proxied call would be rejected with HTTP 400.
4.  Passes responses through **verbatim**.  Desktop does not parse, rewrite,
    summarize, re-rank, or re-derive a task, a question, an allowed action, or
    an evidence field.  The one exception is the notification count, which is
    narrowed further in :mod:`engine.portal.notifications`.

If the console is not running, not built with the ``console`` extra, or does
not implement a route yet, the seam surfaces a structured
``portal_upstream_unavailable`` result rather than inventing an answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

import httpx

#: Loopback host every upstream request is addressed to and presented as.
FLOW_LOOPBACK_HOST = "127.0.0.1"

FLOW_TIMEOUT_S = 20.0

#: Largest protected artifact relayed to a phone.  A crop far exceeding this is
#: refused rather than streamed, keeping the mobile path bounded.
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024

MAX_DECISION_BODY_BYTES = 16 * 1024

RouteMethod = Literal["GET", "POST"]


@dataclass(frozen=True)
class FlowRoute:
    """One allowlisted upstream console route.

    Attributes:
        method: HTTP method.
        template: Upstream path with ``{run_id}`` / ``{action_id}`` slots.
        protected: Whether the response may contain protected local evidence.
            Protected responses are always served ``no-store`` and are never
            cacheable by the phone's service worker.
    """

    method: RouteMethod
    template: str
    protected: bool


#: The complete set of upstream console routes the portal may reach.  This is
#: the wire contract with ``openadapt-flow``; every entry exists on Flow
#: ``origin/main`` today (``openadapt_flow/console/app.py``).
FLOW_ROUTES: dict[str, FlowRoute] = {
    "session": FlowRoute("GET", "/api/session", protected=False),
    "notification": FlowRoute("GET", "/api/attention/notification", protected=False),
    "tasks": FlowRoute("GET", "/api/attention", protected=True),
    "task_detail": FlowRoute("GET", "/api/attention/{run_id}", protected=True),
    "task_action": FlowRoute(
        "POST", "/api/attention/{run_id}/actions/{action_id}", protected=True
    ),
    "task_evidence": FlowRoute(
        "GET", "/api/runs/{run_id}/artifact", protected=True
    ),
}


class FlowConsoleUnavailable(RuntimeError):
    """The attended console could not be reached or refused at the transport level."""

    def __init__(self, message: str, *, reason: str = "unavailable") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class FlowResponse:
    """A verbatim upstream response.

    ``json`` is populated for JSON responses; ``content`` carries raw bytes for
    protected artifacts.  Desktop never inspects either.
    """

    status: int
    json: Any | None
    content: bytes | None
    media_type: str


@dataclass
class FlowConsoleClient:
    """A narrow client for one running attended console.

    Args:
        port: The loopback port the console bound.
        access_token: The console's bearer capability.  It stays on this
            machine: a paired phone receives a portal session token instead and
            never learns this value.
        csrf_token: The console's CSRF token, required for mutations.
        client: Injected HTTP client (tests supply a transport-backed one).
    """

    port: int
    access_token: str
    csrf_token: str = ""
    client: Any | None = None

    @property
    def base_url(self) -> str:
        return f"http://{FLOW_LOOPBACK_HOST}:{self.port}"

    def _headers(self, *, mutation: bool) -> dict[str, str]:
        # Host is implied by base_url. Origin must be the loopback origin or
        # Flow's same-origin check refuses the request.
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Origin": self.base_url,
        }
        if mutation:
            headers["Content-Type"] = "application/json"
            headers["X-OpenAdapt-CSRF"] = self.csrf_token
        return headers

    def _http(self) -> Any:
        return self.client if self.client is not None else httpx

    def request(
        self,
        route: str,
        *,
        run_id: str = "",
        action_id: str = "",
        params: dict[str, str] | None = None,
        json_body: Any | None = None,
    ) -> FlowResponse:
        """Perform one allowlisted upstream call.

        Raises:
            FlowConsoleUnavailable: If ``route`` is not allowlisted, the body is
                oversized, or the console cannot be reached.
        """
        spec = FLOW_ROUTES.get(route)
        if spec is None:
            raise FlowConsoleUnavailable(
                f"{route!r} is not an allowlisted attended console route",
                reason="route_not_allowed",
            )
        path = spec.template
        if "{run_id}" in path:
            path = path.replace("{run_id}", quote(str(run_id), safe=""))
        if "{action_id}" in path:
            path = path.replace("{action_id}", quote(str(action_id), safe=""))
        mutation = spec.method == "POST"
        if mutation:
            import json as _json

            encoded = _json.dumps(json_body if json_body is not None else {}).encode()
            if len(encoded) > MAX_DECISION_BODY_BYTES:
                raise FlowConsoleUnavailable(
                    "The decision payload is too large", reason="body_too_large"
                )
        try:
            response = self._http().request(
                spec.method,
                f"{self.base_url}{path}",
                headers=self._headers(mutation=mutation),
                params=params or None,
                json=json_body if mutation else None,
                timeout=FLOW_TIMEOUT_S,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise FlowConsoleUnavailable(
                "The local OpenAdapt decision service is not reachable"
            ) from exc

        media_type = str(response.headers.get("content-type", "")).split(";")[0].strip()
        if media_type.startswith("image/"):
            content = response.content
            if len(content) > MAX_ARTIFACT_BYTES:
                raise FlowConsoleUnavailable(
                    "That evidence image is too large to relay to a phone",
                    reason="artifact_too_large",
                )
            return FlowResponse(response.status_code, None, content, media_type)
        try:
            payload = response.json()
        except (ValueError, TypeError):
            payload = None
        return FlowResponse(
            response.status_code,
            payload,
            None,
            media_type or "application/json",
        )
