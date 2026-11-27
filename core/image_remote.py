"""Official-API adapters for paid image providers — env credentials only.

Transport is injectable so every external call is mockable. No real network
request is made unless an explicit, one-shot, cost-bound approval is consumed.
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from core import image_assets, image_governance as gov
from core.image_providers import BLOCKED, NOT_VERIFIED, ImageResult, _Base

OPENAI_MODELS = ("gpt-image-2.6-sunburst", "gpt-image-2.6-flare")
GEMINI_MODELS = ("gemini-nano-banana-2.1", "gemini-3-pro-image")
BFL_MODELS = ("flux-2-max", "flux-2-pro")


def _default_http(method, url, headers, body, timeout):
    data = None
    if isinstance(body, dict):
        data = json.dumps(body).encode("utf-8")
    elif isinstance(body, bytes):
        data = body
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, dict(exc.headers), raw


def _default_download(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


_http = _default_http
_download = _default_download


def _redact_error(code: int, raw: bytes) -> str:
    detail = ""
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
        detail = str((obj.get("error") or {}).get("message") or
                     obj.get("message") or "")[:180]
    except (ValueError, AttributeError):
        detail = raw.decode("utf-8", "replace")[:120]
    return "provider-http-%s: %s" % (code, detail)


class _RemoteBase(_Base):
    env_name = ""
    models: tuple[str, ...] = ()
    endpoint = ""

    def _key(self) -> str | None:
        return os.environ.get(self.env_name) or None

    def status(self) -> dict:
        cfg = next((p for p in self._cfg().get("providers", [])
                    if p.get("id") == self.id), {})
        return {"provider": self.id, "model": ",".join(self.models),
                "status": BLOCKED if not self._key() else "READY",
                "credential_env": self.env_name,
                "credential_presence": "SET" if self._key() else "unset",
                "active": bool(cfg.get("enabled")),
                "paid": bool(cfg.get("paid")),
                "endpoint": self.endpoint,
                "models": list(self.models),
                "authenticated": False,
                "note": "NOT CONNECTED until a real authenticated call succeeds"}

    def _authorize(self, req: dict) -> ImageResult:
        def deny(reason):
            return ImageResult.fail(reason, BLOCKED, provider=self.id)
        if not self._key():
            return deny("missing-credential:" + self.env_name)
        model = str(req.get("model") or "")
        if model not in self.models:
            return deny("model-not-supported:" + model)
        if not req.get("approval_id"):
            return deny("approval-required")
        if not req.get("confirmed_cost_usd"):
            return deny("cost-must-be-disclosed")
        verdict = gov.consume(str(req["approval_id"]),
                              gov.payload_from(str(req.get("project") or ""), req))
        if verdict.get("status") != "PASS":
            return deny(str(verdict.get("error") or "approval-invalid"))
        return ImageResult(status="PASS", ok=True, provider=self.id,
            evidence={"cost_usd": verdict.get("cost_usd")})

    def _json_call(self, url, headers, body, timeout):
        code, _, raw = _http("POST", url, headers, body, timeout)
        if code < 200 or code >= 300:
            raise RuntimeError(_redact_error(code, raw))
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            raise RuntimeError("malformed-json-response")

    def _finish(self, project, rel, data, meta, auth) -> ImageResult:
        try:
            asset = image_assets.save(project, rel, data, meta)
        except ValueError as exc:
            return ImageResult.fail("invalid-png:" + str(exc), "FAIL")
        gov.record_usage(str(meta.get("approval_id") or ""), self.id,
                         str(meta.get("model") or ""), meta.get("usage") or {})
        return ImageResult(status="PASS", ok=True, path=asset["path"],
            format="png", width=asset["dimensions"][0],
            height=asset["dimensions"][1], sha256=asset["sha256"],
            provider=self.id, model=str(meta.get("model") or ""),
            cost_note="estimated $%s (provider billing authoritative)" %
                      auth.evidence.get("cost_usd"),
            evidence={"asset": asset, "approval_id": meta.get("approval_id")})

    def generate(self, prompt: str, **kw) -> ImageResult:
        raise NotImplementedError

    def edit(self, req: dict) -> ImageResult:
        raise NotImplementedError


class OpenAIImageProvider(_RemoteBase):
    id = "openai-image"
    title = "OpenAI GPT Image 2.6"
    env_name = "OPENAI_API_KEY"
    models = OPENAI_MODELS
    endpoint = "https://api.openai.com/v1/images"

    def _headers(self):
        return {"Authorization": "Bearer " + str(self._key()),
                "Content-Type": "application/json"}

    def _call(self, req, operation):
        auth = self._authorize(req)
        if getattr(auth, "status", None) != "PASS":
            return auth
        prompt = str(req.get("prompt") or "")
        model = str(req.get("model"))
        payload = {"model": model, "prompt": prompt, "n": 1,
                   "output_format": "png",
                   "size": str(req.get("size") or "1024x1024"),
                   "quality": str(req.get("quality") or "high")}
        if req.get("transparent"):
            payload["background"] = "transparent"
        if operation == "edit":
            refs = req.get("references") or []
            if not refs or len(refs) > 16:
                return ImageResult.fail("reference-count-invalid (1..16)", "FAIL")
            payload["images"] = [{"image_url": r} for r in refs]
            url = self.endpoint + "/edits"
        else:
            url = self.endpoint + "/generations"
        try:
            obj = self._json_call(url, self._headers(), payload,
                                  float(req.get("timeout_s") or 180))
            b64 = ((obj.get("data") or [{}])[0] or {}).get("b64_json")
            if not b64:
                return ImageResult.fail("no-b64_json-in-response", "FAIL")
            data = base64.b64decode(b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            return ImageResult.fail(str(exc)[:300], "FAIL")
        meta = {"provider": self.id, "model": model, "operation": operation,
                "prompt_sha256": gov.request_hash({"prompt": prompt}),
                "reference_hashes": [__import__("hashlib").sha256(
                    str(r).encode()).hexdigest() for r in (req.get("references") or [])],
                "usage": obj.get("usage") or {}, "approval_id": req.get("approval_id"),
                "estimated_cost_usd": auth.evidence.get("cost_usd"), "size": payload["size"]}
        return self._finish(str(req.get("project") or ""),
                            str(req.get("output") or "assets/generated/image.png"),
                            data, meta, auth)

    def generate(self, prompt: str, **kw) -> ImageResult:
        kw["prompt"] = prompt
        kw["operation"] = "edit" if kw.get("references") else "generate"
        return self._call(kw, kw["operation"])

    def edit(self, req: dict) -> ImageResult:
        req["operation"] = "edit"
        return self._call(req, "edit")


class GeminiImageProvider(_RemoteBase):
    id = "gemini-image"
    title = "Google Gemini Nano Banana"
    env_name = "GEMINI_API_KEY"
    models = GEMINI_MODELS
    endpoint = "https://generativelanguage.googleapis.com/v1beta/interactions"

    def _headers(self):
        return {"x-goog-api-key": str(self._key()),
                "Content-Type": "application/json",
                "Api-Revision": "2026-05-20"}

    def _call(self, req, operation):
        auth = self._authorize(req)
        if getattr(auth, "status", None) != "PASS":
            return auth
        ref_urls = req.get("references") or []
        max_refs = 5 if req.get("model") == "gemini-3-pro-image" else 14
        if len(ref_urls) > max_refs:
            return ImageResult.fail("reference-count-exceeded:%d" % max_refs, "FAIL")
        parts = [{"type": "text", "text": str(req.get("prompt") or "")}]
        for ref in ref_urls:
            parts.append({"type": "image", "mime_type": "image/png", "data": ref})
        body = {"model": str(req.get("model")), "input": parts,
                "response_format": {"type": "image", "mime_type": "image/png",
                                    "image_size": str(req.get("quality") or "1K")}}
        if req.get("previous_interaction_id"):
            body["previous_interaction_id"] = req["previous_interaction_id"]
        try:
            obj = self._json_call(self.endpoint, self._headers(), body,
                                  float(req.get("timeout_s") or 180))
            b64 = ((obj.get("output_image") or {}).get("data") or
                   (obj.get("outputImage") or {}).get("data"))
            if not b64:
                return ImageResult.fail("no-output_image-in-response", "FAIL")
            data = base64.b64decode(b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            return ImageResult.fail(str(exc)[:300], "FAIL")
        meta = {"provider": self.id, "model": req.get("model"),
                "operation": operation,
                "prompt_sha256": gov.request_hash({"prompt": req.get("prompt")}),
                "reference_hashes": [__import__("hashlib").sha256(
                    str(r).encode()).hexdigest() for r in ref_urls],
                "interaction_id": obj.get("id"),
                "multi_turn": bool(req.get("previous_interaction_id")),
                "usage": obj.get("usage") or {}, "approval_id": req.get("approval_id"),
                "estimated_cost_usd": auth.evidence.get("cost_usd")}
        return self._finish(str(req.get("project") or ""),
                            str(req.get("output") or "assets/generated/image.png"),
                            data, meta, auth)

    def generate(self, prompt: str, **kw) -> ImageResult:
        kw["prompt"] = prompt
        kw["operation"] = "generate"
        return self._call(kw, "generate")

    def edit(self, req: dict) -> ImageResult:
        req["operation"] = "edit"
        return self._call(req, "edit")


class BFLImageProvider(_RemoteBase):
    id = "bfl-image"
    title = "Black Forest Labs FLUX.2"
    env_name = "BFL_API_KEY"
    models = BFL_MODELS
    endpoint = "https://api.bfl.ai/v1"

    def _headers(self):
        return {"x-key": str(self._key()), "accept": "application/json",
                "Content-Type": "application/json"}

    def _call(self, req, operation):
        auth = self._authorize(req)
        if getattr(auth, "status", None) != "PASS":
            return auth
        refs = req.get("references") or []
        if len(refs) > 8:
            return ImageResult.fail("reference-count-exceeded:8", "FAIL")
        if refs and any(str(r).startswith("/") or str(r).startswith("data:")
                        for r in refs):
            return ImageResult.fail(
                "bfl-local-reference-upload-not-verified: use a provider "
                "reachable URL (official docs show URL inputs)", "BLOCKED")
        size = str(req.get("size") or "1024x1024").split("x")
        try:
            w, h = int(size[0]), int(size[1])
        except (ValueError, IndexError):
            return ImageResult.fail("invalid-size", "FAIL")
        payload = {"prompt": str(req.get("prompt") or ""), "width": w, "height": h}
        for i, r in enumerate(refs[:8]):
            payload["input_image" if i == 0 else "input_image_%d" % (i + 1)] = r
        try:
            created = self._json_call(self.endpoint + "/" + req.get("model"),
                                      self._headers(), payload,
                                      float(req.get("timeout_s") or 180))
            polling_url = created.get("polling_url")
            if not polling_url:
                return ImageResult.fail("no-polling_url", "FAIL")
            deadline = time.time() + float(req.get("timeout_s") or 180)
            result = None
            while time.time() < deadline:
                code, _, raw = _http("GET", polling_url, self._headers(), None, 30)
                if code < 200 or code >= 300:
                    raise RuntimeError(_redact_error(code, raw))
                result = json.loads(raw.decode("utf-8", "replace"))
                status = str(result.get("status") or "")
                if status == "Ready":
                    break
                if status in ("Error", "Failed"):
                    return ImageResult.fail("provider-task-" + status, "FAIL")
                time.sleep(1.0)
            sample = ((result or {}).get("result") or {}).get("sample")
            if not sample:
                return ImageResult.fail("polling-timeout-or-no-sample", "FAIL")
            data = _download(sample, 60)
        except Exception as exc:  # noqa: BLE001
            return ImageResult.fail(str(exc)[:300], "FAIL")
        meta = {"provider": self.id, "model": req.get("model"),
                "operation": operation,
                "prompt_sha256": gov.request_hash({"prompt": req.get("prompt")}),
                "reference_hashes": [__import__("hashlib").sha256(
                    str(r).encode()).hexdigest() for r in refs],
                "request_id": created.get("id"), "usage": result.get("usage") or {},
                "approval_id": req.get("approval_id"),
                "estimated_cost_usd": auth.evidence.get("cost_usd")}
        return self._finish(str(req.get("project") or ""),
                            str(req.get("output") or "assets/generated/image.png"),
                            data, meta, auth)

    def generate(self, prompt: str, **kw) -> ImageResult:
        kw["prompt"] = prompt
        kw["operation"] = "generate"
        return self._call(kw, "generate")

    def edit(self, req: dict) -> ImageResult:
        req["operation"] = "edit"
        return self._call(req, "edit")



OPENROUTER_MODEL = "recraft/recraft-v4.1-flash-v2:free"
OPENROUTER_ASPECTS = ("1:1", "2:1", "1:2", "3:2", "2:3", "4:3", "3:4",
                      "5:4", "4:5", "16:9", "9:16", "auto")


def _aspect_from_size(size):
    try:
        w, h = (int(x) for x in str(size or "").lower().split("x")[:2])
    except (ValueError, TypeError):
        return "auto"
    if w <= 0 or h <= 0:
        return "auto"
    target = w / h
    return min(OPENROUTER_ASPECTS[:-1],
               key=lambda a: abs((int(a.split(":")[0]) / int(a.split(":")[1])) - target))


class OpenRouterImageProvider(_RemoteBase):
    """FREE OpenRouter Image API — exact model only, zero cost, no fallback."""
    id = "openrouter-image"
    title = "OpenRouter Recraft V4.1 Flash V2 (free)"
    env_name = "OPENROUTER_API_KEY"
    models = (OPENROUTER_MODEL,)
    endpoint = "https://openrouter.ai/api/v1/images"

    def _headers(self):
        return {"Authorization": "Bearer " + str(self._key()),
                "Content-Type": "application/json"}

    def status(self):
        st = super().status()
        st["exact_model"] = OPENROUTER_MODEL
        st["free_only"] = True
        st["supports_editing"] = False
        st["supports_reference_images"] = False
        st["eu_routing"] = "BLOCKED (official model descriptor)"
        return st

    def _authorize(self, req: dict) -> ImageResult:
        model = str(req.get("model") or "")
        if model != OPENROUTER_MODEL:
            return ImageResult.fail("non-free-or-invalid-model-rejected:" + model[:80],
                                    BLOCKED, provider=self.id)
        if str(req.get("operation") or "generate") != "generate":
            return ImageResult.fail("editing-not-supported-by-free-model", BLOCKED,
                                    provider=self.id)
        if req.get("references"):
            return ImageResult.fail("reference-images-not-supported-by-free-model",
                                    BLOCKED, provider=self.id)
        if str(req.get("region") or "").lower() in ("eu", "europe", "eu-only"):
            return ImageResult.fail("eu-in-region-routing-unavailable", BLOCKED,
                                    provider=self.id)
        auth = super()._authorize(req)
        if getattr(auth, "status", None) != "PASS":
            return auth
        est = gov.estimate(self.id, model)
        amount = est.get("amount_usd")
        if not est.get("ok") or amount is None or float(amount) != 0.0:
            return ImageResult.fail("zero-cost-estimate-required", BLOCKED,
                                    provider=self.id)
        return auth

    def generate(self, prompt: str, **kw) -> ImageResult:
        req = dict(kw)
        req["prompt"] = prompt
        req["operation"] = str(req.get("operation") or "generate")
        auth = self._authorize(req)
        if getattr(auth, "status", None) != "PASS":
            return auth
        if int(req.get("n") or 1) != 1:
            return ImageResult.fail("n-must-be-1-for-free-model", "FAIL",
                                    provider=self.id)
        aspect = str(req.get("aspect_ratio") or _aspect_from_size(req.get("size")))
        if aspect not in OPENROUTER_ASPECTS:
            return ImageResult.fail("aspect-ratio-not-supported:" + aspect, "FAIL",
                                    provider=self.id)
        payload = {"model": OPENROUTER_MODEL, "prompt": prompt, "n": 1,
                   "aspect_ratio": aspect, "response_format": "b64_json"}
        try:
            # ONE call only: no retry, no fallback, no second provider.
            code, _, raw = _http("POST", self.endpoint, self._headers(), payload,
                                 float(req.get("timeout_s") or 120))
        except Exception as exc:  # noqa: BLE001
            return ImageResult.fail("transport-error:" + str(exc)[:200], "FAIL",
                                    provider=self.id)
        low = raw.decode("utf-8", "replace").lower()
        message = _redact_error(code, raw)
        if code == 401:
            return ImageResult.fail("authentication-failed:401", "FAIL", provider=self.id)
        if code == 403:
            if any(k in low for k in ("eu", "region", "routing", "in-region")):
                return ImageResult.fail("regional-routing-unavailable:403", BLOCKED,
                                        provider=self.id)
            return ImageResult.fail("access-blocked:403", "FAIL", provider=self.id)
        if code == 429:
            return ImageResult.fail("rate-limited:429-no-retry", "FAIL", provider=self.id)
        if code == 402:
            return ImageResult.fail("insufficient-credits:402", "BLOCKED", provider=self.id)
        if code == 404:
            return ImageResult.fail("model-or-provider-unavailable:404", BLOCKED,
                                    provider=self.id)
        if code >= 500:
            return ImageResult.fail("upstream-error:%s" % code, "FAIL", provider=self.id)
        if code < 200 or code >= 300:
            return ImageResult.fail(message, "FAIL", provider=self.id)
        try:
            obj = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            return ImageResult.fail("malformed-json-response", "FAIL", provider=self.id)
        returned = str(obj.get("model") or OPENROUTER_MODEL)
        if returned != OPENROUTER_MODEL:
            return ImageResult.fail("returned-model-mismatch:" + returned[:80], "FAIL",
                                    provider=self.id)
        usage = obj.get("usage") or {}
        try:
            cost = float(usage.get("cost") or 0)
        except (TypeError, ValueError):
            return ImageResult.fail("invalid-billing-information", "FAIL", provider=self.id)
        if cost != 0.0:
            return ImageResult.fail("unexpected-nonzero-charge:%.4f" % cost, "BLOCKED",
                                    provider=self.id)
        items = obj.get("data") or []
        if not items or items[0].get("url"):
            return ImageResult.fail("no-inline-b64-response", "FAIL", provider=self.id)
        b64 = items[0].get("b64_json")
        if not b64:
            return ImageResult.fail("no-b64_json-in-response", "FAIL", provider=self.id)
        try:
            data = base64.b64decode(b64, validate=True)
        except Exception:  # noqa: BLE001
            return ImageResult.fail("malformed-base64-image", "FAIL", provider=self.id)
        item = (obj.get("data") or [{}])[0] or {}
        diagnostics = {"response_keys": sorted(obj.keys()),
                       "data_item_keys": sorted(item.keys()),
                       "media_type": item.get("media_type"),
                       "decoded_bytes": len(data),
                       "decoded_magic": data[:12].hex(),
                       "decoded_text": data[:160].decode("utf-8", "replace")}
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            try:
                from PIL import Image
                with Image.open(io.BytesIO(data)) as im:
                    source_format = im.format or "unknown"
                    im.load()
                    converted = im.convert("RGBA") if im.mode not in (
                        "RGB", "RGBA") else im.copy()
                buf = io.BytesIO()
                converted.save(buf, format="PNG")
                data = buf.getvalue()
                diagnostics["converted_from"] = source_format
            except Exception:  # noqa: BLE001 — never treat text/JSON as an image
                return ImageResult.fail(
                    "not-a-decodable-image:%s magic=%s text=%s" % (
                        diagnostics.get("media_type"), diagnostics["decoded_magic"],
                        diagnostics["decoded_text"][:80]), "FAIL", provider=self.id,
                    evidence={"diagnostics": diagnostics})
        meta = {"provider": self.id, "model": OPENROUTER_MODEL, "operation": "generate",
                "prompt_sha256": gov.request_hash({"prompt": prompt}),
                "reference_hashes": [], "usage": usage, "billing_cost_usd": cost,
                "billing_verified": True, "response_model": returned,
                "aspect_ratio": aspect, "approval_id": req.get("approval_id"),
                "estimated_cost_usd": auth.evidence.get("cost_usd"),
                "response_diagnostics": diagnostics}
        return self._finish(str(req.get("project") or ""),
                            str(req.get("output") or "assets/generated/image.png"),
                            data, meta, auth)

    def edit(self, req: dict) -> ImageResult:
        return ImageResult.fail("editing-not-supported-by-free-model", BLOCKED,
                                provider=self.id)

# registered lazily by core.image_providers._all() to avoid circular imports
PROVIDERS = (OpenAIImageProvider(), GeminiImageProvider(), BFLImageProvider(),
             OpenRouterImageProvider())
