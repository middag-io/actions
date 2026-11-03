<?php

declare(strict_types=1);

/**
 * Fixture theme functions for the wp-theme-check dry run.
 */
function example_theme_setup(): void
{
    add_theme_support('title-tag');
}

add_action('after_setup_theme', 'example_theme_setup');
