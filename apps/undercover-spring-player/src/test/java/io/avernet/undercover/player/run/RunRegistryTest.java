package io.avernet.undercover.player.run;

import static org.assertj.core.api.Assertions.assertThat;

import io.avernet.undercover.player.run.RunRegistry.AbortOutcome;
import io.avernet.undercover.player.run.RunRegistry.AbortStatus;
import io.avernet.undercover.player.run.RunRegistry.RunRegistration;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;

class RunRegistryTest {

    @Test
    void abortsOnlyTheExactActiveRun() {
        RunRegistry registry = new RunRegistry();
        RunRegistration registration = registry.register("run-1", "session-1", "session-1");

        AbortOutcome outcome = registry.abort("run-1", "session-1");

        assertThat(outcome.status()).isEqualTo(AbortStatus.ABORTED);
        assertThat(outcome.run()).isSameAs(registration.run());
        assertThat(registration.run().isTerminal()).isTrue();
    }

    @Test
    void abortIsIdempotentAfterTerminalState() {
        RunRegistry registry = new RunRegistry();
        registry.register("run-1", "session-1", "session-1");
        registry.abort("run-1", "session-1");

        AbortOutcome second = registry.abort("run-1", "session-1");

        assertThat(second.status()).isEqualTo(AbortStatus.ALREADY_TERMINAL);
        assertThat(second.run()).isNull();
    }

    @Test
    void unknownAndMismatchedRunsAreRejected() {
        RunRegistry registry = new RunRegistry();
        registry.register("run-1", "session-1", "session-1");

        assertThat(registry.abort("missing", "session-1").status()).isEqualTo(AbortStatus.NOT_FOUND);
        assertThat(registry.abort("run-1", "other-session").status()).isEqualTo(AbortStatus.SCOPE_MISMATCH);
    }

    @Test
    void lateTerminalClaimLosesAfterAbort() {
        RunRegistry registry = new RunRegistry();
        PlayerRun run = registry.register("run-1", "session-1", "session-1").run();
        registry.abort("run-1", "session-1");

        assertThat(registry.claimTerminal(run)).isFalse();
    }

    @Test
    void exactlyOneConcurrentAbortOrFinalWins() throws Exception {
        RunRegistry registry = new RunRegistry();
        PlayerRun run = registry.register("run-1", "session-1", "session-1").run();
        CyclicBarrier barrier = new CyclicBarrier(2);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<Boolean> terminal = executor.submit(() -> {
                barrier.await(1, TimeUnit.SECONDS);
                return registry.claimTerminal(run);
            });
            Future<AbortOutcome> abort = executor.submit(() -> {
                barrier.await(1, TimeUnit.SECONDS);
                return registry.abort("run-1", "session-1");
            });

            int winners = (terminal.get(1, TimeUnit.SECONDS) ? 1 : 0)
                    + (abort.get(1, TimeUnit.SECONDS).status() == AbortStatus.ABORTED ? 1 : 0);
            assertThat(winners).isEqualTo(1);
        } finally {
            executor.shutdownNow();
        }
    }
}
