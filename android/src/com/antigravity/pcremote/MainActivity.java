package com.antigravity.pcremote;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.util.Log;
import android.view.KeyEvent;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.JavascriptInterface;
import android.webkit.JsResult;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;
import org.json.JSONObject;

public class MainActivity extends Activity {
    private WebView webView;
    private volatile java.net.ServerSocket oauthServerSocket;
    private static final String APP_HOST = "pcremote.local";
    private static final String APP_URL = "https://" + APP_HOST + "/index.html";
    private ValueCallback<Uri[]> mFilePathCallback;
    private static final int FILECHOOSER_RESULTCODE = 1001;

    public class AndroidBridge {
        private final Context context;

        AndroidBridge(Context ctx) {
            this.context = ctx;
        }

        @JavascriptInterface
        public void showToast(final String msg) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    Toast.makeText(context, msg, Toast.LENGTH_SHORT).show();
                }
            });
        }

        @JavascriptInterface
        public void showNotification(final String title, final String body) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    try {
                        NotificationManager nm = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
                        if (nm == null) return;
                        String channelId = "pc_remote_agent";
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                            NotificationChannel chan = new NotificationChannel(channelId, "Agent Status", NotificationManager.IMPORTANCE_HIGH);
                            chan.setDescription("Antigravity Agent Notifications");
                            chan.enableVibration(true);
                            nm.createNotificationChannel(chan);
                        }
                        Notification.Builder builder;
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                            builder = new Notification.Builder(context, channelId);
                        } else {
                            builder = new Notification.Builder(context);
                        }
                        builder.setContentTitle(title)
                               .setContentText(body)
                               .setSmallIcon(android.R.drawable.ic_dialog_info)
                               .setAutoCancel(true);
                        nm.notify((int) System.currentTimeMillis(), builder.build());
                    } catch (Exception ignored) {}
                }
            });
        }

        @JavascriptInterface
        public void vibrate(final long ms) {
            try {
                Vibrator v = (Vibrator) context.getSystemService(Context.VIBRATOR_SERVICE);
                if (v != null && v.hasVibrator()) {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE));
                    } else {
                        v.vibrate(ms);
                    }
                }
            } catch (Exception ignored) {}
        }

        @JavascriptInterface
        public void copyToClipboard(final String text) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    try {
                        ClipboardManager cm = (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
                        if (cm != null) {
                            ClipData clip = ClipData.newPlainText("PC Remote", text);
                            cm.setPrimaryClip(clip);
                            Toast.makeText(context, "Скопировано в буфер", Toast.LENGTH_SHORT).show();
                        }
                    } catch (Exception ignored) {}
                }
            });
        }

        @JavascriptInterface
        public void openInExternalBrowser(final String url) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    try {
                        Uri parsed = Uri.parse(url);
                        String scheme = parsed.getScheme();
                        if (scheme == null || (!"http".equalsIgnoreCase(scheme) && !"https".equalsIgnoreCase(scheme))) {
                            return;
                        }
                        Intent intent = new Intent(Intent.ACTION_VIEW, parsed);
                        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        context.startActivity(intent);
                    } catch (Exception ignored) {}
                }
            });
        }

        @JavascriptInterface
        public boolean sendWakeOnLan(final String macStr) {
            try {
                String cleanMac = (macStr == null ? "A83B7676208F" : macStr).replaceAll("[^0-9A-Fa-f]", "");
                if (cleanMac.length() != 12) return false;
                final byte[] macBytes = new byte[6];
                for (int i = 0; i < 6; i++) {
                    macBytes[i] = (byte) Integer.parseInt(cleanMac.substring(i * 2, i * 2 + 2), 16);
                }
                final byte[] bytes = new byte[6 + 16 * 6];
                for (int i = 0; i < 6; i++) {
                    bytes[i] = (byte) 0xff;
                }
                for (int i = 6; i < bytes.length; i += 6) {
                    System.arraycopy(macBytes, 0, bytes, i, 6);
                }
                new Thread(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            java.net.InetAddress addr = java.net.InetAddress.getByName("255.255.255.255");
                            java.net.DatagramPacket packet = new java.net.DatagramPacket(bytes, bytes.length, addr, 9);
                            java.net.DatagramSocket socket = new java.net.DatagramSocket();
                            socket.setBroadcast(true);
                            socket.send(packet);
                            socket.close();
                        } catch (Exception ignored) {}
                    }
                }).start();
                return true;
            } catch (Exception e) {
                return false;
            }
        }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Immersive dark status bar
        Window window = getWindow();
        window.clearFlags(WindowManager.LayoutParams.FLAG_TRANSLUCENT_STATUS);
        window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
        window.setStatusBarColor(0xFF09090B);
        window.setNavigationBarColor(0xFF09090B);

        // Auto request permissions for audio & notifications if needed
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            try {
                String[] perms;
                if (Build.VERSION.SDK_INT >= 33) {
                    perms = new String[]{Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS};
                } else {
                    perms = new String[]{Manifest.permission.RECORD_AUDIO};
                }
                requestPermissions(perms, 2001);
            } catch (Exception ignored) {}
        }

        webView = new WebView(this);
        webView.setBackgroundColor(0xFF09090B);
        webView.setLongClickable(true);
        webView.setHapticFeedbackEnabled(true);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        webView.addJavascriptInterface(new AndroidBridge(this), "AndroidBridge");

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                    Uri origin = request.getOrigin();
                    if (origin != null && APP_HOST.equalsIgnoreCase(origin.getHost())) {
                        java.util.List<String> allowed = new java.util.ArrayList<String>();
                        for (String res : request.getResources()) {
                            if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(res)) {
                                allowed.add(res);
                            }
                        }
                        if (!allowed.isEmpty()) {
                            request.grant(allowed.toArray(new String[0]));
                            return;
                        }
                    }
                    request.deny();
                }
            }

            @Override
            public boolean onJsAlert(WebView view, String url, String message, final JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                    .setTitle("PC Remote")
                    .setMessage(message)
                    .setPositiveButton(android.R.string.ok, new DialogInterface.OnClickListener() {
                        @Override
                        public void onClick(DialogInterface dialog, int which) {
                            result.confirm();
                        }
                    })
                    .setCancelable(false)
                    .show();
                return true;
            }

            @Override
            public boolean onConsoleMessage(ConsoleMessage cm) {
                Log.d("PCRemote", cm.message() + " [L:" + cm.lineNumber() + "]");
                return true;
            }

            @Override
            public boolean onShowFileChooser(WebView webView, ValueCallback<Uri[]> filePathCallback, WebChromeClient.FileChooserParams fileChooserParams) {
                if (mFilePathCallback != null) {
                    mFilePathCallback.onReceiveValue(null);
                    mFilePathCallback = null;
                }
                mFilePathCallback = filePathCallback;

                Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("image/*");
                try {
                    startActivityForResult(Intent.createChooser(intent, "Выберите скриншот или фото"), FILECHOOSER_RESULTCODE);
                } catch (Exception e) {
                    mFilePathCallback = null;
                    return false;
                }
                return true;
            }
        });

        // Intercept local https://pcremote.local/ requests from packaged assets
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (APP_HOST.equalsIgnoreCase(uri.getHost())) {
                    String path = uri.getPath();
                    if (path == null || path.equals("/") || path.isEmpty()) {
                        path = "/index.html";
                    }
                    if (path.startsWith("/")) {
                        path = path.substring(1);
                    }
                    if (path.contains("..") || path.contains("\\")) {
                        return null;
                    }
                    try {
                        InputStream stream = getAssets().open(path);
                        String mimeType = getMimeType(path);
                        Map<String, String> headers = new HashMap<String, String>();
                        headers.put("Access-Control-Allow-Origin", "*");
                        return new WebResourceResponse(mimeType, "UTF-8", 200, "OK", headers, stream);
                    } catch (IOException e) {
                        return null;
                    }
                }
                return super.shouldInterceptRequest(view, request);
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (uri != null) {
                    String host = uri.getHost();
                    String scheme = uri.getScheme();
                    if (host != null && !APP_HOST.equalsIgnoreCase(host)) {
                        if ("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme)) {
                            try {
                                Intent intent = new Intent(Intent.ACTION_VIEW, uri);
                                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                                startActivity(intent);
                                return true;
                            } catch (Exception ignored) {
                                return false;
                            }
                        }
                        return true;
                    }
                }
                return false;
            }
        });

        setContentView(webView);
        webView.loadUrl(APP_URL);

        // Background loopback OAuth callback interceptor on phone port 8765
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    oauthServerSocket = new java.net.ServerSocket();
                    oauthServerSocket.setReuseAddress(true);
                    oauthServerSocket.bind(new java.net.InetSocketAddress(java.net.InetAddress.getByName("127.0.0.1"), 8765), 5);
                    while (!isDestroyed()) {
                        java.net.Socket socket = oauthServerSocket.accept();
                        try {
                            java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.InputStreamReader(socket.getInputStream()));
                            String line = reader.readLine();
                            if (line != null && line.contains("/api/antigravity/auth/callback")) {
                                String code = null;
                                int codeIdx = line.indexOf("code=");
                                if (codeIdx != -1) {
                                    int endIdx = line.indexOf(" ", codeIdx);
                                    if (endIdx == -1) endIdx = line.length();
                                    int ampIdx = line.indexOf("&", codeIdx);
                                    if (ampIdx != -1 && ampIdx < endIdx) endIdx = ampIdx;
                                    code = line.substring(codeIdx + 5, endIdx);
                                }
                                String html = "<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Авторизовано</title><style>body{background:#09090B;color:#FAFAFA;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center;padding:20px;}.card{background:#18181B;border:1px solid #27272A;border-radius:16px;padding:32px 24px;max-width:380px;}h1{color:#10B981;font-size:20px;margin:12px 0 8px;}p{color:#A1A1AA;font-size:14px;line-height:1.5;}</style></head><body><div class='card'><div style='font-size:48px'>🎉</div><h1>Аккаунт подключен!</h1><p>Авторизация успешно передана в приложение PC Remote и Antigravity. Можете закрыть эту вкладку и вернуться в приложение.</p></div></body></html>";
                                byte[] b = html.getBytes("UTF-8");
                                java.io.OutputStream out = socket.getOutputStream();
                                out.write(("HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: " + b.length + "\r\nConnection: close\r\n\r\n").getBytes("UTF-8"));
                                out.write(b);
                                out.flush();
                                socket.close();

                                if (code != null) {
                                    final String finalCode = java.net.URLDecoder.decode(code, "UTF-8");
                                    final String safeJsArg = JSONObject.quote(finalCode);
                                    runOnUiThread(new Runnable() {
                                        @Override
                                        public void run() {
                                            if (webView != null) {
                                                webView.evaluateJavascript("if (typeof handleOAuthCodeReceived === 'function') handleOAuthCodeReceived(" + safeJsArg + ");", null);
                                            }
                                        }
                                    });
                                }
                            } else {
                                socket.close();
                            }
                        } catch (Exception ignored) {
                            try { socket.close(); } catch (Exception ignore2) {}
                        }
                    }
                } catch (Exception ignored) {}
            }
        }).start();
    }

    @Override
    protected void onDestroy() {
        if (oauthServerSocket != null) {
            try { oauthServerSocket.close(); } catch (Exception ignored) {}
            oauthServerSocket = null;
        }
        super.onDestroy();
    }

    private String getMimeType(String path) {
        if (path.endsWith(".html")) return "text/html";
        if (path.endsWith(".js")) return "application/javascript";
        if (path.endsWith(".css")) return "text/css";
        if (path.endsWith(".json")) return "application/json";
        if (path.endsWith(".png")) return "image/png";
        if (path.endsWith(".jpg") || path.endsWith(".jpeg")) return "image/jpeg";
        if (path.endsWith(".webp")) return "image/webp";
        if (path.endsWith(".svg")) return "image/svg+xml";
        return "application/octet-stream";
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == FILECHOOSER_RESULTCODE) {
            if (mFilePathCallback == null) return;
            Uri[] results = null;
            if (resultCode == Activity.RESULT_OK && data != null) {
                String dataString = data.getDataString();
                if (dataString != null) {
                    results = new Uri[]{Uri.parse(dataString)};
                } else if (data.getClipData() != null) {
                    final int numSelectedFiles = data.getClipData().getItemCount();
                    results = new Uri[numSelectedFiles];
                    for (int i = 0; i < numSelectedFiles; i++) {
                        results[i] = data.getClipData().getItemAt(i).getUri();
                    }
                }
            }
            mFilePathCallback.onReceiveValue(results);
            mFilePathCallback = null;
        } else {
            super.onActivityResult(requestCode, resultCode, data);
        }
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView != null && webView.canGoBack()) {
            webView.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }
}
