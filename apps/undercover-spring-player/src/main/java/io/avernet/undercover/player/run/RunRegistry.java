package io.avernet.undercover.player.run;

import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedQueue;
import org.springframework.stereotype.Component;

@Component
public class RunRegistry {

    private static final int MAX_TOMBSTONES = 1_024;

    private final ConcurrentHashMap<String, PlayerRun> activeRuns = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<String, String> terminalSessions = new ConcurrentHashMap<>();
    private final ConcurrentLinkedQueue<String> tombstoneOrder = new ConcurrentLinkedQueue<>();

    public RunRegistration register(String runId, String sessionKey, String sessionId) {
        PlayerRun candidate = new PlayerRun(runId, sessionKey, sessionId);
        PlayerRun existing = activeRuns.putIfAbsent(runId, candidate);
        return existing == null
                ? new RunRegistration(candidate, true)
                : new RunRegistration(existing, false);
    }

    public boolean claimTerminal(PlayerRun run) {
        if (!run.claimTerminal()) {
            return false;
        }
        // Tombstone first so abort() never observes neither an active run nor a terminal record.
        rememberTerminal(run.runId(), run.sessionKey());
        activeRuns.remove(run.runId(), run);
        return true;
    }

    public AbortOutcome abort(String runId, String sessionKey) {
        PlayerRun active = activeRuns.get(runId);
        if (active != null) {
            if (!active.sessionKey().equals(sessionKey)) {
                return new AbortOutcome(AbortStatus.SCOPE_MISMATCH, null);
            }
            if (claimTerminal(active)) {
                return new AbortOutcome(AbortStatus.ABORTED, active);
            }
            return new AbortOutcome(AbortStatus.ALREADY_TERMINAL, active);
        }

        String terminalSession = terminalSessions.get(runId);
        if (terminalSession == null) {
            return new AbortOutcome(AbortStatus.NOT_FOUND, null);
        }
        if (!terminalSession.equals(sessionKey)) {
            return new AbortOutcome(AbortStatus.SCOPE_MISMATCH, null);
        }
        return new AbortOutcome(AbortStatus.ALREADY_TERMINAL, null);
    }

    private void rememberTerminal(String runId, String sessionKey) {
        if (terminalSessions.putIfAbsent(runId, sessionKey) == null) {
            tombstoneOrder.add(runId);
        }
        while (terminalSessions.size() > MAX_TOMBSTONES) {
            String oldest = tombstoneOrder.poll();
            if (oldest == null) {
                break;
            }
            terminalSessions.remove(oldest);
        }
    }

    public record RunRegistration(PlayerRun run, boolean created) {
    }

    public record AbortOutcome(AbortStatus status, PlayerRun run) {
    }

    public enum AbortStatus {
        ABORTED,
        ALREADY_TERMINAL,
        NOT_FOUND,
        SCOPE_MISMATCH
    }
}
