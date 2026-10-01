"""
SQLiteFusedProfileStore 单元测试

测试融合结果的存储功能。
"""

import pytest
from unittest.mock import patch
from src.infra.adapters.sqlite_fused_profile_store import SQLiteFusedProfileStore
from src.domain.exceptions import DuplicateFusionException, FusionNotFoundException
from src.domain.models.profile_fusion import FusedProfileRecord
from src.domain.models.profile_fusion import ConversationTurn, ConversationStats


class TestSQLiteFusedProfileStore:
    """SQLiteFusedProfileStore 测试类"""

    @pytest.fixture
    def store(self):
        """创建内存数据库存储"""
        return SQLiteFusedProfileStore(db_path=":memory:")

    @pytest.fixture
    def sample_record(self):
        """创建示例融合记录"""
        return FusedProfileRecord(
            fusion_id="fusion-test-001",
            fusion_mode="bot_profile_fuse",
            env="prod",
            group_id="grp-test-001",
            driver_bot_id="bot-driver-001",
            question="这个架构方案有什么风险？",
            participant_ids="wrk_arch:default,wrk_dba:default",
            participant_profile_snapshot=[
                {
                    "worker_id": "wrk_arch:default",
                    "soul_md": "I am an architect",
                    "identity_md": "10 years experience",
                    "memory_md": "Worked on many projects",
                    "skills": ["code_review", "deployment"],
                },
                {
                    "worker_id": "wrk_dba:default",
                    "soul_md": "I am a DBA",
                    "identity_md": "5 years experience",
                    "memory_md": "Database expert",
                    "skills": ["sql_tuning", "backup_recovery"],
                },
            ],
            fuse_detail={
                "name": "专家团队：架构师+DBA",
                "description": "融合后的超级BOT",
                "persona": "综合架构与数据库能力...",
                "skills": ["code_review", "deployment", "sql_tuning", "backup_recovery"],
            },
        )

    def test_save_and_find_by_key(self, store, sample_record):
        """测试保存和查询"""
        # 保存
        fusion_id = store.save(sample_record)
        assert fusion_id == sample_record.fusion_id

        # 查询 - 使用 find_by_key 而不是 find_by_fusion_id
        found = store.find_by_key(fusion_id)
        assert found is not None
        assert found.fusion_id == sample_record.fusion_id
        assert found.fusion_mode == "bot_profile_fuse"
        assert found.group_id == "grp-test-001"

    def test_duplicate_fusion_id(self, store, sample_record):
        """测试重复 fusion_id 异常"""
        store.save(sample_record)

        with pytest.raises(DuplicateFusionException):
            store.save(sample_record)

    def test_find_by_participant(self, store, sample_record):
        """测试根据参与者查询"""
        store.save(sample_record)

        records = store.find_by_participant("wrk_arch:default")
        assert len(records) == 1
        assert records[0].fusion_id == sample_record.fusion_id

    def test_find_by_group(self, store, sample_record):
        """测试根据群组查询"""
        store.save(sample_record)

        records = store.find_by_group("grp-test-001")
        assert len(records) == 1
        assert records[0].fusion_id == sample_record.fusion_id

    def test_append_turn(self, store, sample_record):
        """测试追加对话轮次"""
        store.save(sample_record)

        # 追加对话 - turn_index 会被 store 忽略并自动设置
        turn = ConversationTurn(
            turn_index=0,  # 会被 store 自动设置
            question="这个系统架构有什么问题？",
            sender_id="usr_001",
            sender_name="用户A",
            answer_content="根据我的分析...",
            answer_response_ms=850,
        )
        store.append_turn(sample_record.fusion_id, turn)

        # 查询验证
        found = store.find_by_key(sample_record.fusion_id)
        assert found is not None
        assert len(found.conversation_recent) == 1
        assert found.conversation_stats["turns"] == 1
        assert found.conversation_stats["avg_response_ms"] == 850.0

    def test_append_multiple_turns(self, store, sample_record):
        """测试追加多个对话轮次"""
        store.save(sample_record)

        # 追加多个对话
        for i in range(5):
            turn = ConversationTurn(
                turn_index=0,  # 会被 store 自动设置
                question=f"问题 {i + 1}",
                sender_id="usr_001",
                answer_content=f"回答 {i + 1}",
                answer_response_ms=800 + i * 100,
            )
            store.append_turn(sample_record.fusion_id, turn)

        # 查询验证
        found = store.find_by_key(sample_record.fusion_id)
        assert found is not None
        assert len(found.conversation_recent) == 5
        assert found.conversation_stats["turns"] == 5
        # 平均响应时间: (800 + 900 + 1000 + 1100 + 1200) / 5 = 1000
        assert found.conversation_stats["avg_response_ms"] == 1000.0

    def test_append_turn_with_tokens(self, store, sample_record):
        """测试追加对话轮次时 token 统计更新"""
        store.save(sample_record)

        # 第一次对话，带 token
        turn1 = ConversationTurn(
            turn_index=0,
            question="问题1",
            sender_id="usr_001",
            answer_content="回答1",
            answer_response_ms=500,
            question_token=50,
            response_token=1000,
        )
        store.append_turn(sample_record.fusion_id, turn1)

        found = store.find_by_key(sample_record.fusion_id)
        assert found.conversation_stats["turns"] == 1
        assert found.conversation_stats["avg_response_ms"] == 500.0
        assert found.conversation_stats["avg_question_token"] == 50.0
        assert found.conversation_stats["avg_response_token"] == 1000.0

        # 第二次对话，带 token
        turn2 = ConversationTurn(
            turn_index=0,
            question="问题2",
            sender_id="usr_001",
            answer_content="回答2",
            answer_response_ms=700,
            question_token=60,
            response_token=1200,
        )
        store.append_turn(sample_record.fusion_id, turn2)

        found = store.find_by_key(sample_record.fusion_id)
        assert found.conversation_stats["turns"] == 2
        assert found.conversation_stats["avg_response_ms"] == 600.0  # (500 + 700) / 2
        assert found.conversation_stats["avg_question_token"] == 55.0  # (50 + 60) / 2
        assert found.conversation_stats["avg_response_token"] == 1100.0  # (1000 + 1200) / 2

    def test_append_turn_without_tokens(self, store, sample_record):
        """测试追加对话轮次时不传 token，token 统计保持 0"""
        store.save(sample_record)

        turn = ConversationTurn(
            turn_index=0,
            question="问题1",
            sender_id="usr_001",
            answer_content="回答1",
            answer_response_ms=500,
        )
        store.append_turn(sample_record.fusion_id, turn)

        found = store.find_by_key(sample_record.fusion_id)
        assert found.conversation_stats["turns"] == 1
        assert found.conversation_stats["avg_response_ms"] == 500.0
        # 不传 token 时，token 统计保持默认值 0
        assert found.conversation_stats["avg_question_token"] == 0
        assert found.conversation_stats["avg_response_token"] == 0

    def test_append_turn_mixed_tokens(self, store, sample_record):
        """测试混合传 token 和不传 token 的场景"""
        store.save(sample_record)

        # 第一轮：带 token
        turn1 = ConversationTurn(
            turn_index=0,
            question="问题1",
            sender_id="usr_001",
            answer_content="回答1",
            answer_response_ms=500,
            question_token=50,
            response_token=1000,
        )
        store.append_turn(sample_record.fusion_id, turn1)

        # 第二轮：不带 token
        turn2 = ConversationTurn(
            turn_index=0,
            question="问题2",
            sender_id="usr_001",
            answer_content="回答2",
            answer_response_ms=700,
        )
        store.append_turn(sample_record.fusion_id, turn2)

        found = store.find_by_key(sample_record.fusion_id)
        assert found.conversation_stats["turns"] == 2
        assert found.conversation_stats["avg_response_ms"] == 600.0  # (500 + 700) / 2
        # token 统计只有第一轮的值
        assert found.conversation_stats["avg_question_token"] == 50.0  # 保持不变
        assert found.conversation_stats["avg_response_token"] == 1000.0  # 保持不变

    def test_get_conversation(self, store, sample_record):
        """测试获取对话记录"""
        store.save(sample_record)

        # 追加对话
        for i in range(3):
            turn = ConversationTurn(
                turn_index=0,  # 会被 store 自动设置
                question=f"问题 {i + 1}",
                sender_id="usr_001",
                answer_content=f"回答 {i + 1}",
            )
            store.append_turn(sample_record.fusion_id, turn)

        # 获取对话
        result = store.get_conversation(sample_record.fusion_id)
        assert result is not None
        assert result["total_turns"] == 3
        assert len(result["turns"]) == 3

    def test_get_conversation_pagination(self, store, sample_record):
        """测试对话记录分页"""
        store.save(sample_record)

        # 追加对话
        for i in range(10):
            turn = ConversationTurn(
                turn_index=0,  # 会被 store 自动设置
                question=f"问题 {i + 1}",
                sender_id="usr_001",
            )
            store.append_turn(sample_record.fusion_id, turn)

        # 分页获取
        result = store.get_conversation(sample_record.fusion_id, offset=5, limit=3)
        assert result is not None
        assert result["total_turns"] == 10
        assert len(result["turns"]) == 3

    def test_update_status(self, store, sample_record):
        """测试更新状态"""
        store.save(sample_record)

        # 更新状态
        store.update_status(sample_record.fusion_id, "failed", "LLM 调用失败")

        # 验证
        found = store.find_by_key(sample_record.fusion_id)
        assert found is not None
        assert found.status == "failed"
        assert found.fuse_message == "LLM 调用失败"

    def test_update_status_not_found(self, store):
        """测试更新不存在的记录"""
        with pytest.raises(FusionNotFoundException):
            store.update_status("nonexistent", "failed", "Not found")

    def test_exists(self, store, sample_record):
        """测试存在性检查"""
        assert not store.exists(sample_record.fusion_id)

        store.save(sample_record)

        assert store.exists(sample_record.fusion_id)

    def test_count(self, store, sample_record):
        """测试计数"""
        assert store.count() == 0

        store.save(sample_record)

        assert store.count() == 1
        assert store.count(fusion_mode="bot_profile_fuse") == 1
        assert store.count(fusion_mode="agent") == 0

    def test_create_for_different_mode(self, store):
        """测试创建不同模式的记录"""
        record = FusedProfileRecord(
            fusion_id="fusion-agent-001",
            fusion_mode="agent",
            group_id="grp-test-002",
            question="请分析这个系统设计",
            participant_ids="wrk_expert:default",
            fuse_detail={
                "perspectives": [],
                "recommendation": {"summary": "建议"},
            },
            driver_bot_id="bot-driver-002",
        )

        store.save(record)

        found = store.find_by_key(record.fusion_id)
        assert found is not None
        assert found.fusion_mode == "agent"
        assert found.question == "请分析这个系统设计"

    def test_participant_ids_comma_separated(self, store, sample_record):
        """测试 participant_ids 逗号分隔存储"""
        store.save(sample_record)

        # 直接查询数据库验证存储格式
        cursor = store._conn.cursor()
        cursor.execute(
            "SELECT participant_ids FROM bcsfuse_fusion_session WHERE fusion_id = ?",
            (sample_record.fusion_id,),
        )
        row = cursor.fetchone()
        assert row is not None
        # 应该是逗号分隔的字符串
        assert "wrk_arch:default" in row[0]
        assert "wrk_dba:default" in row[0]

    def test_close(self, store):
        """测试关闭连接"""
        store.close()
        # 关闭后不应该崩溃


class TestFusedProfileRecord:
    """FusedProfileRecord 模型测试"""

    def test_conversation_turn(self):
        """测试对话轮次模型"""
        turn = ConversationTurn(
            turn_index=1,
            question="问题",
            sender_id="usr_001",
            answer_content="回答",
            answer_response_ms=500,
        )

        assert turn.turn_index == 1
        assert turn.question == "问题"
        assert turn.answer_response_ms == 500

    def test_fused_profile_record_creation(self):
        """测试 FusedProfileRecord 创建"""
        record = FusedProfileRecord(
            fusion_id="test-fusion-id",
            fusion_mode="bot_profile_fuse",
            group_id="grp-test",
            participant_ids="wrk_1,wrk_2",
            participant_profile_snapshot=[
                {"worker_id": "wrk_1", "soul_md": "soul1", "skills": ["skill1"]},
                {"worker_id": "wrk_2", "soul_md": "soul2", "skills": ["skill2"]},
            ],
            fuse_detail={"name": "测试"},
        )

        assert record.fusion_id == "test-fusion-id"
        assert record.fusion_mode == "bot_profile_fuse"
        assert record.group_id == "grp-test"
        assert record.participant_ids == "wrk_1,wrk_2"

    def test_fused_profile_record_to_dict(self):
        """测试 FusedProfileRecord 转字典"""
        record = FusedProfileRecord(
            fusion_id="test-fusion-id",
            fusion_mode="bot_profile_fuse",
            group_id="grp-test",
            participant_ids="wrk_1,wrk_2",
            fuse_detail={"name": "测试"},
        )

        result = record.to_dict()

        assert result["fusion_id"] == "test-fusion-id"
        assert result["fusion_mode"] == "bot_profile_fuse"
        assert result["group_id"] == "grp-test"
        assert result["participant_ids"] == "wrk_1,wrk_2"
        assert "env" in result

    def test_fused_profile_record_from_dict(self):
        """测试从字典创建 FusedProfileRecord"""
        data = {
            "fusion_id": "test-fusion-id",
            "fusion_mode": "bot_profile_fuse",
            "group_id": "grp-test",
            "driver_bot_id": "bot-001",
            "question": "测试问题",
            "participant_ids": "wrk_1,wrk_2",
            "participant_profile_snapshot": [{"worker_id": "wrk_1"}],
            "fuse_detail": {"name": "测试"},
            "conversation_recent": [],
            "conversation_stats": {"turns": 0, "avg_response_ms": 0.0, "avg_question_token": 0.0, "avg_response_token": 0.0},
            "status": "success",
            "env": "pre",
        }

        record = FusedProfileRecord.from_dict(data)

        assert record.fusion_id == "test-fusion-id"
        assert record.fusion_mode == "bot_profile_fuse"
        assert record.participant_ids == "wrk_1,wrk_2"
        assert record.fuse_detail == {"name": "测试"}
        assert record.env == "pre"

    def test_get_participant_ids_list(self):
        """测试获取参与者ID列表"""
        record = FusedProfileRecord(
            fusion_id="test-fusion-id",
            fusion_mode="bot_profile_fuse",
            participant_ids="wrk_1,wrk_2,wrk_3",
        )

        ids_list = record.get_participant_ids_list()
        assert ids_list == ["wrk_1", "wrk_2", "wrk_3"]

    def test_conversation_turn_to_dict(self):
        """测试 ConversationTurn 转字典"""
        turn = ConversationTurn(
            turn_index=1,
            question="测试问题",
            sender_id="usr_001",
            sender_name="用户A",
            answer_content="测试回答",
            answer_response_ms=500,
        )

        result = turn.to_dict()

        assert result["turn_index"] == 1
        assert result["question"] == "测试问题"
        assert result["sender_id"] == "usr_001"
        assert result["sender_name"] == "用户A"
        assert result["answer_content"] == "测试回答"
        assert result["answer_response_ms"] == 500

    def test_conversation_turn_from_dict(self):
        """测试从字典创建 ConversationTurn"""
        data = {
            "turn_index": 1,
            "question": "测试问题",
            "sender_id": "usr_001",
            "sender_name": "用户A",
            "answer_content": "测试回答",
            "answer_response_ms": 500,
        }

        turn = ConversationTurn.from_dict(data)

        assert turn.turn_index == 1
        assert turn.question == "测试问题"
        assert turn.sender_id == "usr_001"
        assert turn.answer_content == "测试回答"

    def test_conversation_stats_default(self):
        """测试 conversation_stats 默认值"""
        record = FusedProfileRecord(
            fusion_id="test-fusion-id",
            fusion_mode="bot_profile_fuse",
        )

        assert record.conversation_stats == {"turns": 0, "avg_response_ms": 0, "avg_question_token": 0, "avg_response_token": 0}
        assert record.conversation_recent == []


class TestEnvIsolation:
    """测试 env 字段的环境隔离功能

    环境隔离规则：
    - 按 fusion_id 精确查询（find_by_key, exists, update, update_status, append_turn）：不加 env 过滤
    - 列表类查询（find_by_participant, find_by_group, count, clear）：加 env 过滤
    """

    @pytest.fixture
    def store(self):
        """创建内存数据库存储"""
        return SQLiteFusedProfileStore(db_path=":memory:")

    def test_env_default_value(self):
        """FusedProfileRecord 默认 env 值来自 get_fusion_env()"""
        record = FusedProfileRecord(
            fusion_id="test-default-env",
            fusion_mode="bot_profile_fuse",
        )
        assert record.env in ("pre", "prod")

    def test_env_explicit_value(self):
        """FusedProfileRecord 可显式设置 env"""
        record = FusedProfileRecord(
            fusion_id="test-explicit-env",
            fusion_mode="bot_profile_fuse",
            env="pre",
        )
        assert record.env == "pre"

    def test_env_in_to_dict(self):
        """to_dict 包含 env 字段"""
        record = FusedProfileRecord(
            fusion_id="test-env-dict",
            fusion_mode="bot_profile_fuse",
            env="pre",
        )
        assert record.to_dict()["env"] == "pre"

    def test_env_from_dict(self):
        """from_dict 读取 env 字段"""
        data = {
            "fusion_id": "test-env-from-dict",
            "fusion_mode": "bot_profile_fuse",
            "env": "pre",
            "participant_ids": "wrk_1",
        }
        record = FusedProfileRecord.from_dict(data)
        assert record.env == "pre"

    def test_env_from_dict_default(self):
        """from_dict 缺少 env 时使用 get_fusion_env() 默认值"""
        data = {
            "fusion_id": "test-env-from-dict-default",
            "fusion_mode": "bot_profile_fuse",
            "participant_ids": "wrk_1",
        }
        record = FusedProfileRecord.from_dict(data)
        assert record.env in ("pre", "prod")

    def test_find_by_key_no_env_filter(self, store):
        """find_by_key 不按 env 过滤，fusion_id 全局唯一"""
        with patch("src.infra.adapters.sqlite_fused_profile_store.get_fusion_env", return_value="prod"):
            record = FusedProfileRecord(
                fusion_id="fusion-no-env-filter",
                fusion_mode="bot_profile_fuse",
                env="pre",  # 记录标记为 pre
                participant_ids="wrk_1",
            )
            store.save(record)

        # 即使当前环境是 prod，也能通过 fusion_id 找到 env=pre 的记录
        with patch("src.infra.adapters.sqlite_fused_profile_store.get_fusion_env", return_value="prod"):
            found = store.find_by_key("fusion-no-env-filter")
            assert found is not None
            assert found.env == "pre"

    def test_count_isolated_by_env(self, store):
        """count 按环境隔离——列表查询加 env 过滤"""
        with patch("src.infra.adapters.sqlite_fused_profile_store.get_fusion_env", return_value="prod"):
            store.save(FusedProfileRecord(
                fusion_id="fusion-prod-001", fusion_mode="bot_profile_fuse",
                env="prod", participant_ids="wrk_1",
            ))

        # 直接插入 pre 记录
        cursor = store._conn.cursor()
        cursor.execute(
            "INSERT INTO bcsfuse_fusion_session (fusion_id, fusion_mode, participant_ids, status, gmt_create, gmt_modify, env) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("fusion-pre-001", "bot_profile_fuse", "wrk_2", "success", "2025-01-01T00:00:00", "2025-01-01T00:00:00", "pre"),
        )
        store._conn.commit()

        with patch("src.infra.adapters.sqlite_fused_profile_store.get_fusion_env", return_value="prod"):
            assert store.count() == 1

        with patch("src.infra.adapters.sqlite_fused_profile_store.get_fusion_env", return_value="pre"):
            assert store.count() == 1

    def test_find_by_group_isolated_by_env(self, store):
        """find_by_group 按环境隔离——列表查询加 env 过滤"""
        with patch("src.infra.adapters.sqlite_fused_profile_store.get_fusion_env", return_value="prod"):
            store.save(FusedProfileRecord(
                fusion_id="fusion-prod-grp", fusion_mode="bot_profile_fuse",
                env="prod", participant_ids="wrk_1", group_id="grp-shared",
            ))

        cursor = store._conn.cursor()
        cursor.execute(
            "INSERT INTO bcsfuse_fusion_session (fusion_id, fusion_mode, participant_ids, status, gmt_create, gmt_modify, env, group_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("fusion-pre-grp", "bot_profile_fuse", "wrk_2", "success", "2025-01-01T00:00:00", "2025-01-01T00:00:00", "pre", "grp-shared"),
        )
        store._conn.commit()

        with patch("src.infra.adapters.sqlite_fused_profile_store.get_fusion_env", return_value="prod"):
            records = store.find_by_group("grp-shared")
            assert len(records) == 1
            assert records[0].env == "prod"

        with patch("src.infra.adapters.sqlite_fused_profile_store.get_fusion_env", return_value="pre"):
            records = store.find_by_group("grp-shared")
            assert len(records) == 1
            assert records[0].env == "pre"

    def test_find_by_participant_isolated_by_env(self, store):
        """find_by_participant 按环境隔离——列表查询加 env 过滤"""
        with patch("src.infra.adapters.sqlite_fused_profile_store.get_fusion_env", return_value="prod"):
            store.save(FusedProfileRecord(
                fusion_id="fusion-prod-part", fusion_mode="bot_profile_fuse",
                env="prod", participant_ids="wrk_shared",
            ))

        cursor = store._conn.cursor()
        cursor.execute(
            "INSERT INTO bcsfuse_fusion_session (fusion_id, fusion_mode, participant_ids, status, gmt_create, gmt_modify, env) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("fusion-pre-part", "bot_profile_fuse", "wrk_shared", "success", "2025-01-01T00:00:00", "2025-01-01T00:00:00", "pre"),
        )
        store._conn.commit()

        with patch("src.infra.adapters.sqlite_fused_profile_store.get_fusion_env", return_value="prod"):
            records = store.find_by_participant("wrk_shared")
            assert len(records) == 1
            assert records[0].env == "prod"

        with patch("src.infra.adapters.sqlite_fused_profile_store.get_fusion_env", return_value="pre"):
            records = store.find_by_participant("wrk_shared")
            assert len(records) == 1
            assert records[0].env == "pre"


class TestConversationStats:
    """ConversationStats 模型测试"""

    def test_conversation_stats_creation(self):
        """测试创建 ConversationStats"""
        stats = ConversationStats(turns=10, avg_response_ms=850.5, avg_question_token=50.0, avg_response_token=1200.0)

        assert stats.turns == 10
        assert stats.avg_response_ms == 850.5
        assert stats.avg_question_token == 50.0
        assert stats.avg_response_token == 1200.0

    def test_conversation_stats_default_values(self):
        """测试默认值"""
        stats = ConversationStats()

        assert stats.turns == 0
        assert stats.avg_response_ms == 0.0
        assert stats.avg_question_token == 0.0
        assert stats.avg_response_token == 0.0

    def test_conversation_stats_to_dict(self):
        """测试转字典"""
        stats = ConversationStats(turns=5, avg_response_ms=720.0, avg_question_token=45.0, avg_response_token=1100.0)

        result = stats.to_dict()

        assert result == {"turns": 5, "avg_response_ms": 720.0, "avg_question_token": 45.0, "avg_response_token": 1100.0}

    def test_conversation_stats_from_dict(self):
        """测试从字典创建"""
        data = {"turns": 15, "avg_response_ms": 650.5, "avg_question_token": 60.0, "avg_response_token": 1300.0}

        stats = ConversationStats.from_dict(data)

        assert stats.turns == 15
        assert stats.avg_response_ms == 650.5
        assert stats.avg_question_token == 60.0
        assert stats.avg_response_token == 1300.0

    def test_conversation_stats_from_dict_partial(self):
        """测试从部分字典创建"""
        stats = ConversationStats.from_dict({})

        assert stats.turns == 0
        assert stats.avg_response_ms == 0.0
        assert stats.avg_question_token == 0.0
        assert stats.avg_response_token == 0.0

    def test_conversation_stats_from_dict_with_instance(self):
        """测试从 ConversationStats 实例创建（直接返回）"""
        original = ConversationStats(turns=20, avg_response_ms=900.0)

        result = ConversationStats.from_dict(original)

        assert result is original

    def test_conversation_stats_update_with_first_response(self):
        """测试第一次响应更新"""
        stats = ConversationStats()

        stats.update_with_new_response(500)

        assert stats.turns == 1
        assert stats.avg_response_ms == 500.0

    def test_conversation_stats_update_with_multiple_responses(self):
        """测试多次响应更新"""
        stats = ConversationStats()

        # 第一次响应
        stats.update_with_new_response(800)
        assert stats.turns == 1
        assert stats.avg_response_ms == 800.0

        # 第二次响应: (800 * 1 + 1000) / 2 = 900
        stats.update_with_new_response(1000)
        assert stats.turns == 2
        assert stats.avg_response_ms == 900.0

        # 第三次响应: (900 * 2 + 700) / 3 = 833.33...
        stats.update_with_new_response(700)
        assert stats.turns == 3
        assert abs(stats.avg_response_ms - 833.33) < 0.01

    def test_conversation_stats_backward_compatibility(self):
        """测试旧数据格式的向后兼容性（缺少 token 字段时自动补默认值）"""
        # 模拟旧数据格式（没有 avg_question_token 和 avg_response_token）
        old_data = {"turns": 10, "avg_response_ms": 850.5}
        stats = ConversationStats.from_dict(old_data)

        # 应该自动补充缺失字段为 0
        assert stats.turns == 10
        assert stats.avg_response_ms == 850.5
        assert stats.avg_question_token == 0.0
        assert stats.avg_response_token == 0.0

    def test_conversation_stats_update_with_tokens(self):
        """测试带 token 参数的更新"""
        stats = ConversationStats()

        # 第一次响应，带 token 信息
        stats.update_with_new_response(response_ms=500, question_token=50, response_token=1000)
        assert stats.turns == 1
        assert stats.avg_response_ms == 500.0
        assert stats.avg_question_token == 50.0
        assert stats.avg_response_token == 1000.0

        # 第二次响应
        stats.update_with_new_response(response_ms=700, question_token=60, response_token=1200)
        assert stats.turns == 2
        assert stats.avg_response_ms == 600.0  # (500 + 700) / 2
        assert stats.avg_question_token == 55.0  # (50 + 60) / 2
        assert stats.avg_response_token == 1100.0  # (1000 + 1200) / 2

        # 第三次响应，不传 token 信息
        stats.update_with_new_response(response_ms=800)
        assert stats.turns == 3
        assert abs(stats.avg_response_ms - 666.67) < 0.01  # (600 * 2 + 800) / 3 ≈ 666.67
        assert stats.avg_question_token == 55.0  # 保持不变
        assert stats.avg_response_token == 1100.0  # 保持不变


if __name__ == "__main__":
    pytest.main([__file__, "-v"])