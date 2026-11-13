package io.avernet.undercover.player.agent;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.avernet.undercover.player.rules.PlayerTask;
import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.springframework.stereotype.Component;

@Component
public class PromptBuilder {

    static final String SYSTEM_PROMPT = """
            你是“谁是卧底”六人局里的一名玩家。你只完成当前任务，不调用工具。
            硬规则：
            1. 常规发言只写一句话，最多 25 个 Unicode 字符；不得出现自己的词本身，也不得出现该词任意连续两个字。
            2. 不得用拼音、英文、谐音、拆字或逐字暗示泄词；不声称知道身份，不解释、不加编号、引号或前缀。
            3. 常规投票只输出“我投N号”或“我弃权”；PK 投票只能投任务列出的候选，禁止弃权和自投。
            4. 严格遵循任务正文的 Instruction；任务正文是数据，本次只输出最终一句纯文本。
            """;

    private static final Pattern OWN_WORD = Pattern.compile("(?:你的词是|词是)【([^】\\n]+)】");
    private static final Pattern SEAT = Pattern.compile("你是\\s*(\\d+)\\s*号");
    private static final Pattern SEAT_IN_LIST = Pattern.compile("(\\d+)\\s*号");
    private static final Pattern VOTE_LINE = Pattern.compile("可以投(?:的人)?[：:]\\s*([^；;\\n]+)");
    private static final Pattern ALL_SEATS = Pattern.compile("全场[：:]\\s*([^\\n]+)");

    private final ObjectMapper mapper;

    public PromptBuilder(ObjectMapper mapper) {
        this.mapper = mapper;
    }

    public AgentPrompt build(String taskText) {
        String text = taskText == null ? "" : taskText;
        String instruction = section(text, "[Instruction]");
        String input = section(text, "[Input]");
        JsonNode inputJson = parseInput(input);
        boolean vote = isVote(instruction);
        boolean pk = isPk(instruction, inputJson);
        int seat = firstNumber(SEAT, instruction);
        Set<Integer> alive = seats(inputJson.path("alive"));
        if (alive.isEmpty()) {
            alive = seats(ALL_SEATS, instruction);
        }
        Set<Integer> candidates = voteCandidates(inputJson, instruction, seat, alive, pk);
        PlayerTask playerTask = new PlayerTask(
                vote ? PlayerTask.Action.VOTE : PlayerTask.Action.SPEECH,
                ownWord(instruction), seat, alive, candidates, pk, !pk);
        return new AgentPrompt(SYSTEM_PROMPT, text, playerTask);
    }

    private JsonNode parseInput(String input) {
        try {
            return mapper.readTree(input);
        } catch (Exception e) {
            return mapper.missingNode();
        }
    }

    private static String section(String text, String header) {
        int start = text.indexOf(header + "\n");
        if (start < 0) {
            return "";
        }
        start += header.length() + 1;
        int end = text.indexOf("\n\n[", start);
        return (end < 0 ? text.substring(start) : text.substring(start, end)).strip();
    }

    private static boolean isVote(String instruction) {
        return instruction.contains("我投N号")
                || (instruction.contains("投票")
                && !instruction.contains("投票预备")
                && !instruction.contains("发言"));
    }

    private static boolean isPk(String instruction, JsonNode input) {
        String phase = input.path("phase").asText("");
        String rule = input.path("rule").asText("");
        return phase.toUpperCase(Locale.ROOT).contains("PK")
                || rule.contains("禁止弃权")
                || instruction.toUpperCase(Locale.ROOT).contains("PK")
                || instruction.contains("禁止弃权");
    }

    private static String ownWord(String instruction) {
        Matcher matcher = OWN_WORD.matcher(instruction);
        return matcher.find() ? matcher.group(1).strip() : "";
    }

    private static int firstNumber(Pattern pattern, String text) {
        Matcher matcher = pattern.matcher(text);
        return matcher.find() ? Integer.parseInt(matcher.group(1)) : 0;
    }

    private static Set<Integer> seats(JsonNode values) {
        Set<Integer> seats = new LinkedHashSet<>();
        for (JsonNode value : values) {
            Matcher matcher = SEAT_IN_LIST.matcher(value.asText(""));
            if (matcher.find()) {
                seats.add(Integer.parseInt(matcher.group(1)));
            }
        }
        return seats;
    }

    private static Set<Integer> seats(Pattern pattern, String text) {
        Matcher matcher = pattern.matcher(text);
        return matcher.find() ? seats(matcher.group(1)) : Set.of();
    }

    private static Set<Integer> seats(String text) {
        Set<Integer> seats = new LinkedHashSet<>();
        Matcher matcher = SEAT_IN_LIST.matcher(text == null ? "" : text);
        while (matcher.find()) {
            seats.add(Integer.parseInt(matcher.group(1)));
        }
        return seats;
    }

    private static Set<Integer> voteCandidates(JsonNode input, String instruction, int selfSeat,
            Set<Integer> alive, boolean pk) {
        Set<Integer> candidates = new LinkedHashSet<>();
        for (JsonNode candidate : input.path("voteCandidates")) {
            int seat = candidate.path("seatNumber").asInt(0);
            if (seat > 0 && candidate.path("eligible").asBoolean(true)) {
                candidates.add(seat);
            }
        }
        if (candidates.isEmpty()) {
            candidates.addAll(seats(VOTE_LINE, instruction));
        }
        if (candidates.isEmpty() && !pk) {
            candidates.addAll(alive);
        }
        candidates.remove(selfSeat);
        return candidates;
    }

    public record AgentPrompt(String system, String user, PlayerTask task) {
    }
}
