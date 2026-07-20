"""Backend capability profiles.

Capabilities describe what the UI may rely on for each provider. They are
intentionally small and concrete: each flag exists because current UI behavior
needs to branch on it.
"""

BACKENDS = {
    "claude": {
        "label": "claude",
        "title": "Claude Frollo Observer",
        "required_cli": "claude",
        "capabilities": {
            "model_selection": True,
            "session_resume": True,
            "subscription_quota": True,
            "cost_usage": True,
            "context_usage": True,
            "reasoning_stream": True,
            "tool_lifecycle": True,
            "approval_events": True,
            "image_input": True,
        },
    },
    "codex": {
        "label": "codex",
        "title": "Codex Frollo Observer",
        "required_cli": "codex",
        "capabilities": {
            "model_selection": False,
            "session_resume": False,
            "subscription_quota": True,
            "cost_usage": False,
            "context_usage": True,
            "reasoning_stream": True,
            "tool_lifecycle": True,
            "approval_events": True,
            "image_input": True,
        },
    },
}


def backend_names():
    return tuple(BACKENDS)


def backend_profile(name):
    try:
        profile = BACKENDS[name]
    except KeyError as exc:
        raise ValueError(f"backend desconhecido: {name}") from exc
    return {
        **profile,
        "capabilities": dict(profile["capabilities"]),
    }


def supports(profile, capability):
    return bool((profile.get("capabilities") or {}).get(capability))
