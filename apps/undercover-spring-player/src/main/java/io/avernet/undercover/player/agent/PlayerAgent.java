package io.avernet.undercover.player.agent;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.avernet.undercover.player.bcn.ChatSendHandler;
import io.avernet.undercover.player.bcn.FrameSender;
import io.avernet.undercover.player.protocol.BcnFrames;
import io.avernet.undercover.player.rules.OutputValidator;
import io.avernet.undercover.player.rules.PlayerTask;
import io.avernet.undercover.player.run.PlayerRun;
import io.avernet.undercover.player.run.RunRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.stereotype.Component;
import reactor.core.Disposable;

@Component
public class PlayerAgent implements ChatSendHandler {

    private static final Logger log = LoggerFactory.getLogger(PlayerAgent.class);

    private final ChatClient chatClient;
    private final PromptBuilder promptBuilder;
    private final OutputValidator validator;
    private final ObjectMapper mapper;
    private final FrameSender sender;
    private final RunRegistry runs;

    public PlayerAgent(ChatClient.Builder chatClientBuilder, PromptBuilder promptBuilder,
            OutputValidator validator, ObjectMapper mapper, FrameSender sender, RunRegistry runs) {
        this.chatClient = chatClientBuilder.build();
        this.promptBuilder = promptBuilder;
        this.validator = validator;
        this.mapper = mapper;
        this.sender = sender;
        this.runs = runs;
    }

    @Override
    public void handle(ChatSendTask task) {
        final PromptBuilder.AgentPrompt prompt;
        try {
            prompt = promptBuilder.build(task.text());
        } catch (RuntimeException e) {
            fail(task.run(), "PROMPT_ERROR", e);
            return;
        }

        StringBuilder content = new StringBuilder();
        try {
            Disposable disposable = chatClient.prompt()
                    .system(prompt.system())
                    .user(prompt.user())
                    .stream()
                    .content()
                    .subscribe(content::append,
                            error -> handleModelFailure(task.run(), prompt.task(), error),
                            () -> complete(task.run(), prompt.task(), content.toString()));
            task.run().setDisposable(disposable);
        } catch (RuntimeException e) {
            handleModelFailure(task.run(), prompt.task(), e);
        }
    }

    private void handleModelFailure(PlayerRun run, PlayerTask task, Throwable error) {
        log.warn("MODEL_ERROR for run_id={}; using rule fallback", run.runId(), error);
        complete(run, task, "");
    }

    private void complete(PlayerRun run, PlayerTask task, String raw) {
        if (!runs.claimTerminal(run)) {
            return;
        }
        final String output;
        try {
            output = validator.validate(raw, task);
        } catch (RuntimeException e) {
            sendError(run, "VALIDATION_ERROR", e);
            return;
        }
        int seq = run.nextTerminalSeq();
        try {
            sender.send(BcnFrames.finalEvent(mapper, run, seq, System.currentTimeMillis(), output));
        } catch (RuntimeException e) {
            log.warn("Cannot send final chat event for run_id={}", run.runId(), e);
        }
    }

    private void fail(PlayerRun run, String code, Throwable error) {
        if (!runs.claimTerminal(run)) {
            return;
        }
        log.warn("{} for run_id={}", code, run.runId(), error);
        sendError(run, code, error);
    }

    private void sendError(PlayerRun run, String code, Throwable error) {
        int seq = run.nextTerminalSeq();
        String message = error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage();
        try {
            sender.send(BcnFrames.errorEvent(mapper, run, seq, System.currentTimeMillis(), code, message));
        } catch (RuntimeException e) {
            log.warn("Cannot send error chat event for run_id={}", run.runId(), e);
        }
    }
}
