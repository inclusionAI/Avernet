package io.avernet.undercover.player.rules;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Set;
import org.junit.jupiter.api.Test;

class OutputValidatorTest {

    private final OutputValidator validator = new OutputValidator();

    @Test
    void truncatesSpeechToTwentyFiveCodePoints() {
        String raw = "一二三四五六七八九十".repeat(3);

        String output = validator.validate(raw, speech("苹果"));

        assertThat(PlayerRules.codePointCount(output)).isEqualTo(25);
    }

    @Test
    void countsEmojiAsOneCodePoint() {
        assertThat(PlayerRules.codePointCount("😀")).isEqualTo(1);

        String output = validator.validate("😀", speech("苹果"));

        assertThat(output).isEqualTo("😀");
    }

    @Test
    void replacesExactOwnWordLeakWithFallback() {
        String output = validator.validate("我觉得苹果很甜", speech("苹果"));

        assertThat(output).doesNotContain("苹果");
        assertThat(PlayerRules.codePointCount(output)).isLessThanOrEqualTo(25);
    }

    @Test
    void rejectsContiguousTwoCharacterFragmentOfOwnWord() {
        String output = validator.validate("这个苹果真甜", speech("红苹果"));

        assertThat(output).doesNotContain("苹果");
        assertThat(output).doesNotContain("红苹果");
    }

    @Test
    void rejectsLeakSplitByWhitespace() {
        String output = validator.validate("这个苹 果真甜", speech("苹果"));

        assertThat(PlayerRules.leaksOwnWord(output, "苹果")).isFalse();
    }

    @Test
    void rejectsLeakHiddenByZeroWidthCharacters() {
        assertThat(PlayerRules.leaksOwnWord("这个苹\u200C果真甜", "苹果")).isTrue();
        assertThat(PlayerRules.leaksOwnWord("这个苹\uFEFF果真甜", "苹果")).isTrue();

        String output = validator.validate("这个苹\u200C果真甜", speech("苹果"));

        assertThat(PlayerRules.leaksOwnWord(output, "苹果")).isFalse();
    }

    @Test
    void garbageVoteFallsBackToLowestEligibleSeat() {
        String output = validator.validate("我觉得三号有点怪", vote(false, Set.of(3, 5)));

        assertThat(output).isEqualTo("我投3号");
    }

    @Test
    void pkAbstentionFallsBackToLowestPkCandidate() {
        String output = validator.validate("我弃权", vote(true, Set.of(2, 4)));

        assertThat(output).isEqualTo("我投2号");
    }

    @Test
    void pkVoteOutsideCandidatesFallsBackToLowestPkCandidate() {
        PlayerTask task = vote(true, Set.of(2, 4));

        assertThat(validator.validate("我投3号", task)).isEqualTo("我投2号");
        assertThat(validator.validate("我投1号", task)).isEqualTo("我投2号");
    }

    @Test
    void oversizedVoteNumberFallsBackInsteadOfThrowing() {
        String output = validator.validate("我投999999999999999999999999号", vote(false, Set.of(2, 4)));

        assertThat(output).isEqualTo("我投2号");
    }

    @Test
    void normalAbstentionIsAccepted() {
        String output = validator.validate("我弃权", vote(false, Set.of(2, 4)));

        assertThat(output).isEqualTo("我弃权");
    }

    @Test
    void acceptsOnlyTheExactVoteFormatAndValidTarget() {
        PlayerTask task = vote(false, Set.of(2, 4));

        assertThat(validator.validate("我投4号", task)).isEqualTo("我投4号");
        assertThat(validator.validate("我投 4 号", task)).isEqualTo("我投2号");
        assertThat(validator.validate("我投5号", task)).isEqualTo("我投2号");
        assertThat(validator.validate("我投1号", task)).isEqualTo("我投2号");
    }

    private static PlayerTask speech(String ownWord) {
        return new PlayerTask(PlayerTask.Action.SPEECH, ownWord, 1, Set.of(1, 2, 3), Set.of(),
                false, true);
    }

    private static PlayerTask vote(boolean pk, Set<Integer> candidates) {
        return new PlayerTask(PlayerTask.Action.VOTE, "苹果", 1, Set.of(1, 2, 3, 4, 5), candidates,
                pk, !pk);
    }
}
