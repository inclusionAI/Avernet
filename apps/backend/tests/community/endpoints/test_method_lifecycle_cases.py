"""Registered lifecycle/composition cases execute real HTTP, DI and MethodStore."""

from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    BindMethodCommand,
    ComposeMethodCommand,
    MethodLifecycleServiceProtocol,
    MethodStateChangeCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from tests.community.factories.context_collab_methods import (
    BASE,
    BINDING,
    HEADERS,
    OWNER,
    seed_method,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_CHILD = "mb_http_child"
_EDGE = "edge_http_case"
_COMPOSE = {
    "relation": "nest",
    "child_binding_id": _CHILD,
    "child_method_kind": "Single",
    "edge_id": _EDGE,
}


def _register(method, suffix, operation, expected, happy):
    params = {"binding_id": BINDING, "edge_id": _EDGE}
    seeded = {}
    body = {"reason": "HTTP lifecycle regression"}
    if operation == "bind":
        body = {"method_kind": "Single", "method_binding_id": "mb_http_new"}
    elif operation == "update":
        body = {"config": {"note": "updated"}, "expected_version": 2}
    elif operation == "compose":
        body = dict(_COMPOSE)

    def seed(world):
        collet_id = seed_method(world, "Single", active=operation != "activate")
        params["collab_context_id"] = collet_id
        seeded["collet_id"] = collet_id
        lifecycle = world.get(MethodLifecycleServiceProtocol)
        if operation == "resume":
            lifecycle.suspend(collet_id, BINDING, MethodStateChangeCommand(), OWNER)
        if operation in {"compose", "detach"}:
            lifecycle.bind(
                collet_id,
                BindMethodCommand(
                    method_kind="Single",
                    method_binding_id=_CHILD,
                    parent_scope_ref="scope://child",
                ),
                OWNER,
            )
        if operation == "detach":
            lifecycle.compose(
                collet_id, BINDING, ComposeMethodCommand(**_COMPOSE), OWNER
            )
        if not happy:
            if operation == "bind":
                body["method_kind"] = "UnknownMethod"
            elif operation == "inspect":
                params["collab_context_id"] = "missing_collet"
            elif operation == "detach":
                params["edge_id"] = "missing_edge"
            else:
                params["binding_id"] = "missing_binding"

    def check(response, world):
        store, axes = world.get(MethodStoreProtocol), current_axes()
        collet_id = seeded["collet_id"]
        binding = store.get_binding(axes, BINDING, collet_id)
        if not happy:
            assert binding.state.value == (
                "BOUND"
                if operation == "activate"
                else "SUSPENDED"
                if operation == "resume"
                else "ACTIVE"
            )
            assert store.get_binding(axes, "mb_http_new", collet_id) is None
            if operation == "detach":
                [edge] = store.list_composition_edges(
                    axes, collet_id, active_only=False
                )
                assert edge.state.value == "ACTIVE"
            return
        data = response.json()["data"]
        if operation in {"compose", "detach"}:
            [edge] = store.list_composition_edges(axes, collet_id, active_only=False)
            assert edge.edge_id == data["edge"]["edge_id"] == _EDGE
            assert edge.state.value == expected
        elif operation == "bind":
            new = store.get_binding(axes, "mb_http_new", collet_id)
            assert new.state.value == data["binding"]["state"] == expected
        else:
            assert binding.state.value == expected
            if operation in {"inspect", "update"}:
                assert binding.config == (
                    {"note": "updated"} if operation == "update" else {}
                )
            if operation != "inspect":
                assert data["accepted"] and data["version"] == binding.version
            else:
                assert data["binding"]["method_binding_id"] == BINDING

    endpoint_test(
        method=method,
        path=BASE + suffix,
        scenario="lifecycle_contract_happy" if happy else "lifecycle_contract_error",
        input=CaseInput(
            path_params=params,
            headers=HEADERS,
            json_body=body if method not in {"GET", "DELETE"} else None,
        ),
        seed=seed,
        expect=ExpectSuccess(json_contains={"code": 200000})
        if happy
        else ExpectError(
            status=422 if operation == "bind" else 404,
            json_contains={"code": 422000 if operation == "bind" else 404000},
        ),
        extra_assertions=(check,),
    )(lambda: None)


for _method, _suffix, _operation, _expected in (
    ("POST", "", "bind", "BOUND"),
    ("GET", "/{binding_id}", "inspect", "ACTIVE"),
    ("PATCH", "/{binding_id}", "update", "ACTIVE"),
    ("POST", "/{binding_id}/activate", "activate", "ACTIVE"),
    ("POST", "/{binding_id}/suspend", "suspend", "SUSPENDED"),
    ("POST", "/{binding_id}/resume", "resume", "ACTIVE"),
    ("POST", "/{binding_id}/terminate", "terminate", "TERMINATED"),
    ("POST", "/{binding_id}/compose", "compose", "ACTIVE"),
    ("DELETE", "/{binding_id}/compose/{edge_id}", "detach", "DETACHED"),
):
    for _happy in (True, False):
        _register(_method, _suffix, _operation, _expected, _happy)
