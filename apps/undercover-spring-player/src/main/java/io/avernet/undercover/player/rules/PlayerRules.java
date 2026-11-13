package io.avernet.undercover.player.rules;

import java.util.OptionalInt;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class PlayerRules {

    public static final int MAX_SPEECH_CODE_POINTS = 25;

    private static final Pattern VOTE_TARGET = Pattern.compile("^我投(\\d+)号$");
    private static final Pattern ABSTAIN = Pattern.compile("^我弃权$");
    private static final Pattern IGNORABLE = Pattern.compile("[\\p{Z}\\p{Cc}\\p{Cf}]+");

    private PlayerRules() {
    }

    public static int codePointCount(String text) {
        return text == null ? 0 : text.codePointCount(0, text.length());
    }

    public static String truncate(String text, int maxCodePoints) {
        if (text == null) {
            return "";
        }
        if (codePointCount(text) <= maxCodePoints) {
            return text;
        }
        return text.substring(0, text.offsetByCodePoints(0, maxCodePoints));
    }

    public static boolean isSafeSpeech(String text, String ownWord) {
        return text != null && !text.isBlank()
                && codePointCount(text) <= MAX_SPEECH_CODE_POINTS
                && !leaksOwnWord(text, ownWord);
    }

    public static boolean leaksOwnWord(String text, String ownWord) {
        if (text == null || ownWord == null || ownWord.isBlank()) {
            return false;
        }
        String word = IGNORABLE.matcher(ownWord).replaceAll("");
        if (word.isEmpty()) {
            return false;
        }
        String compact = IGNORABLE.matcher(text).replaceAll("");
        if (compact.contains(word)) {
            return true;
        }
        int[] codePoints = word.codePoints().toArray();
        for (int i = 0; i + 1 < codePoints.length; i++) {
            if (compact.contains(new String(codePoints, i, 2))) {
                return true;
            }
        }
        return false;
    }

    public static OptionalInt parseVoteSeat(String text) {
        Matcher matcher = VOTE_TARGET.matcher(text == null ? "" : text);
        if (!matcher.matches()) {
            return OptionalInt.empty();
        }
        try {
            return OptionalInt.of(Integer.parseInt(matcher.group(1)));
        } catch (NumberFormatException e) {
            return OptionalInt.empty();
        }
    }

    public static boolean isAbstain(String text) {
        return ABSTAIN.matcher(text == null ? "" : text).matches();
    }
}
