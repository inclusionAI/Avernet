package io.avernet.undercover.player.rules;

import java.util.List;
import java.util.OptionalInt;
import java.util.Set;
import java.util.stream.Collectors;

public final class FallbackResponses {

    private static final List<String> SPEECHES = List.of(
            "这个先不说，我再听听。",
            "我先保留看法，看看后面。",
            "现在信息还不够，先放一放。",
            "我暂时没想好，再观察一下。");

    private FallbackResponses() {
    }

    public static String speech(String ownWord) {
        String word = ownWord == null ? "" : ownWord;
        for (String candidate : SPEECHES) {
            if (PlayerRules.isSafeSpeech(candidate, word)) {
                return candidate;
            }
        }
        Set<Integer> blocked = word.codePoints().boxed().collect(Collectors.toSet());
        StringBuilder filtered = new StringBuilder();
        "这个先不说。".codePoints()
                .filter(codePoint -> !blocked.contains(codePoint))
                .forEach(filtered::appendCodePoint);
        String candidate = filtered.toString().strip();
        return PlayerRules.isSafeSpeech(candidate, word) ? candidate : "……";
    }

    public static String vote(PlayerTask task) {
        OptionalInt seat = lowest(task.voteCandidates(), task.seatNumber());
        if (seat.isEmpty() && !task.pk()) {
            seat = lowest(task.aliveSeats(), task.seatNumber());
        }
        if (seat.isPresent()) {
            return "我投" + seat.getAsInt() + "号";
        }
        if (task.allowAbstain() && !task.pk()) {
            return "我弃权";
        }
        throw new IllegalStateException("No legal " + (task.pk() ? "PK " : "") + "vote target in task");
    }

    private static OptionalInt lowest(Set<Integer> seats, int selfSeat) {
        return seats.stream()
                .filter(seat -> seat > 0 && seat != selfSeat)
                .mapToInt(Integer::intValue)
                .min();
    }
}
