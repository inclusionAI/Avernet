# ── Self-test: just recipe param passing ───────────────────────────────

_test_echo str='' num='0':
    echo "str={{str}} num={{num}}"

test-justified:
    #!/bin/bash
    set -e
    ec=0

    # Test 1: small integer + string (positional args)
    out=$(just _test_echo "hello" "42")
    echo "$out" | grep -q 'str=hello num=42' || { echo "FAIL 1: small int/string"; ec=1; }

    # Test 2: large integer (epoch-style, like date +%s in 2026)
    epoch=1782886920
    out=$(just _test_echo "epoch" "$epoch")
    echo "$out" | grep -q "str=epoch num=$epoch" || { echo "FAIL 2: large int (epoch)"; ec=1; }

    # Test 3: zero
    out=$(just _test_echo "zero" "0")
    echo "$out" | grep -q 'str=zero num=0' || { echo "FAIL 3: zero"; ec=1; }

    # Test 4: empty string
    out=$(just _test_echo "" "99")
    echo "$out" | grep -q 'str= num=99' || { echo "FAIL 4: empty string"; ec=1; }

    # Test 5: SECONDS-style elapsed
    s=127
    out=$(just _test_echo "elapsed" "$s")
    echo "$out" | grep -q "str=elapsed num=$s" || { echo "FAIL 5: seconds-like int"; ec=1; }

    if [[ $ec -eq 0 ]]; then
        echo ""
        echo -e "\033[0;32mAll param-passing tests passed!\033[0m"
    else
        echo ""
        echo -e "\033[0;31mSome tests failed\033[0m"
    fi
    exit $ec
