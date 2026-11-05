<?php

namespace Example\MoodleLibrary;

/** Reads the Moodle release of the host the code runs in. */
final class Probe
{
    public static function release(): string
    {
        global $CFG;

        return (string) ($CFG->release ?? '');
    }
}
