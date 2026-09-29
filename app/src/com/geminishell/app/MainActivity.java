package com.geminishell.app;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;

import androidx.browser.customtabs.CustomTabsClient;
import androidx.browser.customtabs.CustomTabsIntent;
import androidx.browser.customtabs.CustomTabsServiceConnection;
import androidx.browser.customtabs.CustomTabsSession;

import java.util.ArrayList;
import java.util.List;

/**
 * Gemini Shell - a thin, dependency-free launcher for Gemini web.
 *
 * Design intent (deliberately different from "web-to-apk" builders):
 *   - Renders in Chrome via Custom Tabs, NOT android.webkit.WebView.
 *     => real Blink/Chrome engine, real Google mobile layout, no UA spoofing,
 *        and no disallowed_useragent login wall.
 *   - Uses Chrome's own cookie jar, so an existing Chrome Google session is
 *     reused with zero re-authentication.
 *   - No analytics, no ads, no push, no billing, no camera/location/storage.
 *     The only permission requested is INTERNET.
 *   - Toolbar tinted to Gemini's surface color so the transition reads as an
 *     app, not a browser pop-up. URL is revealed only on hover/scroll-up.
 */
public class MainActivity extends Activity {

    private static final String TARGET_URL = "https://gemini.google.com/app";

    /** Chrome stable. Warm-up only; a missing package is not an error. */
    private static final String[] CANDIDATE_PROVIDERS = {
            "com.android.chrome",
            "com.chrome.beta",
            "com.chrome.dev",
            "com.chrome.canary",
            "org.chromium.chrome",
    };

    private CustomTabsServiceConnection connection;
    private CustomTabsSession session;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Warm up Chrome in the background so the tab opens without a cold-start
        // flash. Best-effort: never blocks or breaks the launch path.
        warmUp();

        // Only ever launch the Gemini entry point; ignore stray intents.
        launch(TARGET_URL);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        // If the activity is reused from recents, reopen straight into Gemini.
        launch(TARGET_URL);
    }

    private void warmUp() {
        for (String pkg : CANDIDATE_PROVIDERS) {
            if (!isPackageInstalled(pkg)) continue;
            try {
                connection = new CustomTabsServiceConnection() {
                    @Override
                    public void onCustomTabsServiceConnected(ComponentName name,
                                                            CustomTabsClient client) {
                        client.warmup(0L);
                        session = client.newSession(null);
                        if (session != null) {
                            // Prefetch so the first paint is not empty.
                            List<Bundle> uris = new ArrayList<>();
                            Bundle b = new Bundle();
                            b.putParcelable(CustomTabsIntent.EXTRA_SESSION, null);
                            uris.add(b);
                            session.mayLaunchUrl(Uri.parse(TARGET_URL), null, null);
                        }
                    }

                    @Override
                    public void onServiceDisconnected(ComponentName name) {
                        session = null;
                    }
                };
                CustomTabsClient.bindCustomTabsService(this, pkg, connection);
            } catch (Throwable ignored) {
                // Warm-up is an optimisation only.
            }
            return;
        }
    }

    private void launch(String url) {
        Uri uri = Uri.parse(url);

        CustomTabsIntent.Builder builder = new CustomTabsIntent.Builder(session);

        // Match the Gemini app surface so the top bar does not read as a browser.
        builder.setToolbarColor(0xFF1B1B1B);
        builder.setSecondaryToolbarColor(0xFF1B1B1B);
        builder.setShowTitle(false);

        // Keep the URL hidden until the user deliberately scrolls up.
        builder.setUrlBarHidingEnabled(true);

        // Share is genuinely useful here; the rest of the native action row is
        // noise for a single-page shell, so it is disabled.
        builder.setShareState(CustomTabsIntent.SHARE_STATE_ON);

        // Match the platform's animation so it behaves like an activity switch.
        builder.setStartAnimations(this,
                android.R.anim.slide_in_left, android.R.anim.fade_out);
        builder.setExitAnimations(this,
                android.R.anim.fade_in, android.R.anim.slide_out_right);

        CustomTabsIntent customTabsIntent = builder.build();
        customTabsIntent.intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);

        try {
            customTabsIntent.launchUrl(this, uri);
        } catch (ActivityNotFoundException e) {
            // No Custom Tabs provider (Chrome removed). Degrade to any browser
            // that can handle the URL rather than crashing.
            Intent fallback = new Intent(Intent.ACTION_VIEW, uri);
            fallback.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            try {
                startActivity(fallback);
            } catch (ActivityNotFoundException e2) {
                // Nothing can open the URL; finish cleanly rather than ANR.
            }
        }
        finish();
    }

    private boolean isPackageInstalled(String pkg) {
        try {
            getPackageManager().getPackageInfo(pkg, 0);
            return true;
        } catch (Throwable e) {
            return false;
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (connection != null) {
            try {
                unbindService(connection);
            } catch (Throwable ignored) {
            }
            connection = null;
        }
    }
}