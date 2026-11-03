#!/usr/bin/env bash
# Finishes the Composer dependencies of a WordPress plugin, in the current
# directory, for the distribution archive. php-composer-setup has already
# installed them (production only, or every dependency with Strauss) and
# written the credentials to the global Composer auth.json.
#
# Environment:
#   HAS_STRAUSS    "true" runs "composer strauss" (vendor prefixing), then a
#                  reinstall without dev dependencies.
#
# Without composer.json the script does nothing. When the plugin ships
# scripts/strip-lock-tokens.php, it runs it last, as the plugin's own way to
# remove credentials from composer.lock before packaging.
set -euo pipefail

if [[ ! -f composer.json ]]; then
  echo "No composer.json: skipping the Composer steps."
  exit 0
fi

composer --version
if [[ "${HAS_STRAUSS:-false}" == "true" ]]; then
  composer strauss --no-interaction
  composer install --no-dev --optimize-autoloader --no-scripts --no-interaction --no-progress
  composer dump-autoload --optimize --no-interaction
fi

if [[ -f scripts/strip-lock-tokens.php ]]; then
  php scripts/strip-lock-tokens.php
fi
