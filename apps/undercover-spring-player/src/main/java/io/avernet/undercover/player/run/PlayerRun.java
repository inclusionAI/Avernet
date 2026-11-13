package io.avernet.undercover.player.run;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import reactor.core.Disposable;

public final class PlayerRun {

    private final String runId;
    private final String sessionKey;
    private final String sessionId;
    private final AtomicBoolean terminal = new AtomicBoolean();
    private final AtomicInteger seq = new AtomicInteger();
    private final AtomicReference<Disposable> stream = new AtomicReference<>();

    public PlayerRun(String runId, String sessionKey, String sessionId) {
        this.runId = runId;
        this.sessionKey = sessionKey;
        this.sessionId = sessionId;
    }

    public String runId() {
        return runId;
    }

    public String sessionKey() {
        return sessionKey;
    }

    public String sessionId() {
        return sessionId;
    }

    public boolean isTerminal() {
        return terminal.get();
    }

    boolean claimTerminal() {
        return terminal.compareAndSet(false, true);
    }

    public int nextTerminalSeq() {
        return seq.incrementAndGet();
    }

    public void setDisposable(Disposable disposable) {
        if (disposable == null) {
            return;
        }
        if (terminal.get()) {
            disposable.dispose();
            return;
        }
        if (!stream.compareAndSet(null, disposable)) {
            disposable.dispose();
            return;
        }
        if (terminal.get() && stream.compareAndSet(disposable, null)) {
            disposable.dispose();
        }
    }

    public void cancel() {
        Disposable disposable = stream.getAndSet(null);
        if (disposable != null) {
            disposable.dispose();
        }
    }
}
