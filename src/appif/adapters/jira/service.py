"""Composition factory: wire Jira backends into a WorkTrackingService.

This is the composition root for the Jira adapter. It is the only place that
knows both the domain ``WorkTrackingService`` and the concrete ``JiraAdapter``,
so the domain itself never imports an adapter (see ADR-002). Building the
service programmatically is just ``WorkTrackingService()`` +
``service.register(name, JiraAdapter(...))``; this module adds the YAML-config
convenience used by the CLIs and local development scripts.
"""

from __future__ import annotations

import logging

from appif.adapters.jira._auth import load_config
from appif.adapters.jira.adapter import JiraAdapter
from appif.domain.work_tracking.service import WorkTrackingService

logger = logging.getLogger(__name__)


def create_work_tracking_service(*, auto_load: bool = True) -> WorkTrackingService:
    """Create a ``WorkTrackingService``, optionally pre-loading Jira instances.

    When ``auto_load`` is ``True`` (the default), Jira instances are read from
    the YAML config at ``~/.config/appif/jira/config.yaml`` (or ``APPIF_JIRA_CONFIG``)
    and registered. This exists for the appif CLIs and local development;
    applications should instead construct ``WorkTrackingService()`` and register
    ``JiraAdapter`` instances with credentials supplied programmatically.
    """
    service = WorkTrackingService()
    if auto_load:
        _register_from_config(service)
    return service


def _register_from_config(service: WorkTrackingService) -> None:
    """Register Jira instances from the YAML config file."""
    config = load_config()
    instances = config.get("instances", {})
    default_name = config.get("default")

    for name, instance_cfg in instances.items():
        # Support both flat format and nested jira/confluence format.
        jira_cfg = instance_cfg.get("jira", instance_cfg) if isinstance(instance_cfg, dict) else {}
        url = jira_cfg.get("url", "")
        username = jira_cfg.get("username", "")
        api_token = jira_cfg.get("api_token", "")

        if url and username and api_token:
            try:
                service.register(
                    name,
                    JiraAdapter(url, {"username": username, "api_token": api_token}, instance_name=name),
                    make_default=(name == default_name),
                )
            except Exception:
                # Log but don't fail startup for one bad instance.
                logger.warning("jira.config.instance_skipped", extra={"instance": name})

    # The service makes the first registered instance the default, and any
    # instance matching the config's ``default`` overrides it — so no extra
    # defaulting logic is needed here.
