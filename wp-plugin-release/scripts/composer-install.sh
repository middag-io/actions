#!/usr/bin/env bash
# Installs the production Composer dependencies of a WordPress plugin, in the
# current directory, for the distribution archive.
#
# Environment:
#   HAS_STRAUSS    "true" runs "composer strauss" (vendor prefixing) between a
#                  full install and a reinstall without dev dependencies.
#   COMPOSER_AUTH  optional; read by Composer itself, never printed here.
#
# Without composer.json the script does nothing. When the plugin ships
# scripts/strip-lock-tokens.php, it runs it last, as the plugin's own way to
# remove credentials from composer.lock before packaging.
set -euo pipefail

if [[ ! -f composer.json ]]; then
  echo "No composer.json: skipping the Composer install."
  exit 0
fi

composer --version
if [[ "${HAS_STRAUSS:-false}" == "true" ]]; then
  composer install --optimize-autoloader --no-interaction --no-progress
  composer strauss --no-interaction
  composer install --no-dev --optimize-autoloader --no-scripts --no-interaction --no-progress
  composer dump-autoload --optimize --no-interaction
else
  composer install --no-dev --optimize-autoloader --no-interaction --no-progress
fi

if [[ -f scripts/strip-lock-tokens.php ]]; then
  php scripts/strip-lock-tokens.php
fi
