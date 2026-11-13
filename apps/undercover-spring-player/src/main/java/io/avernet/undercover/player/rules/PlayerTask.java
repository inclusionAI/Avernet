package io.avernet.undercover.player.rules;

import java.util.Set;

public record PlayerTask(Action action, String ownWord, int seatNumber, Set<Integer> aliveSeats,
        Set<Integer> voteCandidates, boolean pk, boolean allowAbstain) {

    public PlayerTask {
        aliveSeats = Set.copyOf(aliveSeats);
        voteCandidates = Set.copyOf(voteCandidates);
        ownWord = ownWord == null ? "" : ownWord;
    }

    public enum Action {
        SPEECH,
        VOTE
    }
}
