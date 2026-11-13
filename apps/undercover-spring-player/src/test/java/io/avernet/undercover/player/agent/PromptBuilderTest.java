package io.avernet.undercover.player.agent;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.avernet.undercover.player.rules.PlayerTask;
import java.util.Set;
import org.junit.jupiter.api.Test;

class PromptBuilderTest {

    private final PromptBuilder builder = new PromptBuilder(new ObjectMapper());

    @Test
    void parsesSpeechTaskAndKeepsFullTaskAsUserPrompt() {
        String task = """
                [State Machine Task]
                node_id: speak_3
                display_name: 3号发言

                [Input]
                {"game":"谁是卧底","round":1,"alive":["1号 老陈","2号 小满","3号 阿浪","4号 喜静"],"history":[]}

                [Upstream Outputs]
                (none)

                [Instruction]
                你是 3 号，你的词是【红苹果】。第 1 轮发言。
                [Input] 含公开规则和开场；[Upstream Outputs] 只含本轮排在你前面的人的原话。
                输出一句话，不超过 25 字，描述你的词。
                只输出这句话：没有编号、引号、JSON、前缀、解释。""";

        PromptBuilder.AgentPrompt prompt = builder.build(task);

        assertThat(prompt.system()).isEqualTo(PromptBuilder.SYSTEM_PROMPT);
        assertThat(prompt.user()).isEqualTo(task);
        assertThat(prompt.task().action()).isEqualTo(PlayerTask.Action.SPEECH);
        assertThat(prompt.task().ownWord()).isEqualTo("红苹果");
        assertThat(prompt.task().seatNumber()).isEqualTo(3);
        assertThat(prompt.task().aliveSeats()).isEqualTo(Set.of(1, 2, 3, 4));
        assertThat(prompt.task().pk()).isFalse();
    }

    @Test
    void parsesPkVoteCandidatesAndForbidsAbstention() {
        String task = """
                [State Machine Task]
                node_id: vote_1
                display_name: 1号投票

                [Input]
                {"game":"谁是卧底","round":2,"phase":"PK 投票","alive":["1号 老陈","2号 小满","3号 阿浪","4号 喜静"],"rule":"仅投本次 PK 玩家，禁止弃权"}

                [Upstream Outputs]
                2号：「我投3号」

                [Instruction]
                第 2 轮PK 投票。只输出「我投N号」（阿拉伯数字），禁止弃权，不超过 8 字，不写理由、解释或前缀。
                你是 1 号，词是【日记】。全场：1号 老陈、2号 小满、3号 阿浪、4号 喜静。
                可以投：2号、3号、4号；不能投自己。
                PK 必须从合法候选中选一位，不能弃权。""";

        PromptBuilder.AgentPrompt prompt = builder.build(task);

        assertThat(prompt.task().action()).isEqualTo(PlayerTask.Action.VOTE);
        assertThat(prompt.task().ownWord()).isEqualTo("日记");
        assertThat(prompt.task().seatNumber()).isEqualTo(1);
        assertThat(prompt.task().voteCandidates()).isEqualTo(Set.of(2, 3, 4));
        assertThat(prompt.task().pk()).isTrue();
        assertThat(prompt.task().allowAbstain()).isFalse();
    }
}
