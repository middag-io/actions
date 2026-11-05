<?php
// Built-in probe of moodle-host-check. Boots the Moodle host and the Composer
// autoloader of the code under test, prints the host versions and reads the
// site configuration back from the database. Writes nothing.
//
// Environment, set by the component:
//   MOODLE_HOST_CHECK_CONFIG    config.php of the Moodle host
//   MOODLE_HOST_CHECK_CODE_DIR  directory of the code under test (composer.json)

define('CLI_SCRIPT', true);

function moodle_host_check_fail(string $message): void {
    fwrite(STDERR, "moodle-host-check: {$message}\n");
    exit(1);
}

$config = (string) getenv('MOODLE_HOST_CHECK_CONFIG');
$codedir = (string) getenv('MOODLE_HOST_CHECK_CODE_DIR');
if ($config === '' || !is_file($config)) {
    moodle_host_check_fail('MOODLE_HOST_CHECK_CONFIG does not name a config.php');
}
$autoload = $codedir . '/vendor/autoload.php';
if (!is_file($autoload)) {
    moodle_host_check_fail("no Composer autoloader at {$autoload}");
}

require($config);
require_once($autoload);

global $CFG, $DB;
printf("release=%s\nbranch=%s\nversion=%s\nphp=%s\ndb=%s\n",
    $CFG->release ?? '?', $CFG->branch ?? '?', $CFG->version ?? '?', PHP_VERSION, get_class($DB));

$stored = $DB->get_field('config', 'value', ['name' => 'version']);
if ((string) $stored !== (string) $CFG->version) {
    moodle_host_check_fail("config.version in the database ({$stored}) differs from \$CFG->version");
}
echo "config.version={$stored}\n";
