<?php
// Fixture probe: boots the Moodle host, then calls the code under test.
define('CLI_SCRIPT', true);
require(getenv('MOODLE_HOST_CHECK_CONFIG'));
require_once(getenv('MOODLE_HOST_CHECK_CODE_DIR') . '/vendor/autoload.php');

echo 'release=' . \Example\MoodleLibrary\Probe::release() . "\n";
