<?php
/**
 * Market Brief Shortcode
 * Usage:
 * [market_brief url="https://raw.githubusercontent.com/USER/REPO/main/data/latest.html"]
 */

if (!defined('ABSPATH')) {
    exit;
}

add_shortcode('market_brief', function ($atts) {
    $atts = shortcode_atts([
        'url' => '',
        'cache_minutes' => '10',
    ], $atts, 'market_brief');

    $raw_url = esc_url_raw($atts['url']);
    if (!$raw_url) {
        return '<div class="market-brief-error">尚未設定 market_brief 的 GitHub raw URL。</div>';
    }

    $cache_minutes = max(1, intval($atts['cache_minutes']));
    $cache_key = 'market_brief_' . md5($raw_url);

    $cached = get_transient($cache_key);
    if ($cached !== false) {
        return market_brief_wrap_html($cached);
    }

    $response = wp_remote_get($raw_url, [
        'timeout' => 12,
        'headers' => [
            'Accept' => 'text/html,text/plain,*/*',
            'User-Agent' => 'WordPress Market Brief Shortcode',
        ],
    ]);

    if (is_wp_error($response)) {
        return '<div class="market-brief-error">每日市場早報暫時無法載入。</div>';
    }

    $status = wp_remote_retrieve_response_code($response);
    $body = wp_remote_retrieve_body($response);

    if ($status !== 200 || empty($body)) {
        return '<div class="market-brief-error">每日市場早報尚未更新。</div>';
    }

    $allowed_html = [
        'article' => ['class' => true],
        'header' => ['class' => true],
        'section' => ['class' => true],
        'div' => ['class' => true],
        'p' => ['class' => true],
        'h1' => ['class' => true],
        'h2' => ['class' => true],
        'h3' => ['class' => true],
        'h4' => ['class' => true],
        'ul' => ['class' => true],
        'ol' => ['class' => true],
        'li' => ['class' => true],
        'strong' => [],
        'em' => [],
        'br' => [],
        'span' => ['class' => true],
        'table' => ['class' => true],
        'thead' => [],
        'tbody' => [],
        'tr' => [],
        'th' => ['class' => true, 'scope' => true],
        'td' => ['class' => true],
        'a' => [
            'href' => true,
            'target' => true,
            'rel' => true,
        ],
    ];

    $clean_html = wp_kses($body, $allowed_html);
    set_transient($cache_key, $clean_html, $cache_minutes * MINUTE_IN_SECONDS);

    return market_brief_wrap_html($clean_html);
});

function market_brief_wrap_html($html) {
    return '<div class="market-brief-box">' . $html . '</div>';
}
