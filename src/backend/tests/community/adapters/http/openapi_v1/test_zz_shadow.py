def test_shadow_report():
    from agentclaw.community.adapters.http.openapi_v1.responses import ENVELOPE_ERRORS

    keys = list(ENVELOPE_ERRORS)
    print("\n--- order ---")
    for i, k in enumerate(keys):
        print(i, k.__module__ + "." + k.__name__, "MRO:", [b.__name__ for b in k.__mro__])
    print("\n--- shadows (earlier key is a base of a later key -> later unreachable) ---")
    for i, a in enumerate(keys):
        for j, b in enumerate(keys):
            if i < j and issubclass(b, a):
                print(f"UNREACHABLE: [{j}] {b.__name__} shadowed by [{i}] {a.__name__}")
    assert True
