<?php

declare(strict_types=1);

namespace Example\Library;

/**
 * Builds greetings.
 */
final class Greeter
{
    /**
     * Returns a greeting for a name.
     *
     * @param string $name Name to greet.
     */
    public function greet(string $name): string
    {
        return 'Hello, ' . $name;
    }
}
