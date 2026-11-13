package io.avernet.undercover.player.bcn;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.avernet.undercover.player.config.BcnProperties;
import io.avernet.undercover.player.protocol.BcnFrames;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.PosixFilePermission;
import java.util.EnumSet;
import java.util.Optional;
import java.util.Set;
import org.springframework.stereotype.Component;

@Component
public class SessionStore {

    private static final Set<PosixFilePermission> OWNER_ONLY = EnumSet.of(
            PosixFilePermission.OWNER_READ,
            PosixFilePermission.OWNER_WRITE);

    private final ObjectMapper mapper;
    private final Path sessionFile;

    public SessionStore(ObjectMapper mapper, BcnProperties properties) {
        this.mapper = mapper;
        this.sessionFile = properties.getSessionFile();
    }

    public Optional<Session> load() {
        if (!Files.exists(sessionFile)) {
            return Optional.empty();
        }
        try {
            JsonNode node = mapper.readTree(Files.readString(sessionFile));
            String sessionToken = BcnFrames.text(node, "token");
            String botUuid = BcnFrames.text(node, "bot_uuid");
            return sessionToken.isEmpty() ? Optional.empty() : Optional.of(new Session(sessionToken, botUuid));
        } catch (IOException e) {
            throw new IllegalStateException("Cannot read BCN session file " + sessionFile, e);
        }
    }

    public void save(String token, String botUuid) {
        if (token == null || token.isBlank()) {
            throw new IllegalArgumentException("BCN token must not be blank");
        }
        try {
            Path directory = sessionFile.toAbsolutePath().getParent();
            Files.createDirectories(directory);
            ObjectNode node = mapper.createObjectNode();
            node.put("token", token);
            if (botUuid != null && !botUuid.isBlank()) {
                node.put("bot_uuid", botUuid.trim());
            }
            Path temporary = Files.createTempFile(directory, "session-", ".tmp");
            try {
                Files.writeString(temporary, mapper.writeValueAsString(node), StandardCharsets.UTF_8);
                setOwnerOnly(temporary);
                Files.move(temporary, sessionFile, StandardCopyOption.REPLACE_EXISTING,
                        StandardCopyOption.ATOMIC_MOVE);
            } finally {
                Files.deleteIfExists(temporary);
            }
        } catch (IOException e) {
            throw new IllegalStateException("Cannot write BCN session file " + sessionFile, e);
        }
    }

    private static void setOwnerOnly(Path path) throws IOException {
        try {
            Files.setPosixFilePermissions(path, OWNER_ONLY);
        } catch (UnsupportedOperationException ignored) {
            // Non-POSIX filesystems cannot express owner-only mode.
        }
    }

    public record Session(String token, String botUuid) {
    }
}
