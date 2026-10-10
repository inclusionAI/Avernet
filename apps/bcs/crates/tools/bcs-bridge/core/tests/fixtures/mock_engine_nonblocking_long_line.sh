#!/usr/bin/env bash
# Emit one JSONL event through a non-blocking stdout descriptor. This models
# an engine's long JSONL write on hosts with a small pipe buffer.
exec perl -MFcntl -e '
  fcntl(STDOUT, F_SETFL, O_NONBLOCK) or die "set stdout nonblocking: $!";
  my $line = q({"type":"item.completed","item":{"type":"agent_message","text":"})
    . ("x" x 7000)
    . q("}})
    . "\n";
  syswrite(STDOUT, $line);
'
