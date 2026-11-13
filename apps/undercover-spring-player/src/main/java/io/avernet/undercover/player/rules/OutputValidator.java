package io.avernet.undercover.player.rules;

import java.util.OptionalInt;
import java.util.Set;
import org.springframework.stereotype.Component;

@Component
public class OutputValidator {

    public String validate(String raw, PlayerTask task) {
        String text = normalize(raw);
        return task.action() == PlayerTask.Action.VOTE
                ? validateVote(text, task)
                : validateSpeech(text, task);
    }

    private String validateSpeech(String text, PlayerTask task) {
        String truncated = PlayerRules.truncate(text, PlayerRules.MAX_SPEECH_CODE_POINTS);
        return PlayerRules.isSafeSpeech(truncated, task.ownWord())
                ? truncated
                : FallbackResponses.speech(task.ownWord());
    }

    private String validateVote(String text, PlayerTask task) {
        String truncated = PlayerRules.truncate(text, PlayerRules.MAX_SPEECH_CODE_POINTS);
        if (PlayerRules.isAbstain(truncated)) {
            return task.pk() || !task.allowAbstain()
                    ? FallbackResponses.vote(task)
                    : "我弃权";
        }
        OptionalInt target = PlayerRules.parseVoteSeat(truncated);
        if (target.isEmpty()) {
            return FallbackResponses.vote(task);
        }
        Set<Integer> allowed = task.pk() || !task.voteCandidates().isEmpty()
                ? task.voteCandidates()
                : task.aliveSeats();
        int seat = target.getAsInt();
        return seat != task.seatNumber() && allowed.contains(seat)
                ? "我投" + seat + "号"
                : FallbackResponses.vote(task);
    }

    private static String normalize(String raw) {
        return raw == null ? "" : raw.strip().replaceAll("\\s+", " ");
    }
}
