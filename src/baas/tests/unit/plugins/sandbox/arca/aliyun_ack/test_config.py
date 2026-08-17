"""Tests for the AliyunAckTemplateConfig model and DI wiring."""

from __future__ import annotations

from secbaas.community.plugins.sandbox.arca.aliyun_ack._config_type import (
    AliyunAckTemplateConfig,
    build_aliyun_ack_template,
)


def test_build_template_merges_id():
    raw = {
        "cluster": {"kubeconfig": "kc", "endpoint": "e", "region": "r"},
        "pod": {"image": "img", "envs": {"A": "b"}, "service_account": "sa"},
    }
    t = build_aliyun_ack_template("ALIYUN_ACK_TEMPLATE_default", raw)
    assert isinstance(t, AliyunAckTemplateConfig)
    assert t.template_id == "ALIYUN_ACK_TEMPLATE_default"
    assert t.cluster.kubeconfig == "kc"
    assert t.pod.image == "img"
    assert t.pod.envs == {"A": "b"}
    assert t.pod.service_account == "sa"


def test_build_template_defaults():
    t = AliyunAckTemplateConfig(template_id="ALIYUN_ACK_TEMPLATE_default")
    assert t.cluster.endpoint == ""
    assert t.pod.image == "ubuntu:22.04"
    assert t.pod.namespace == "default"
