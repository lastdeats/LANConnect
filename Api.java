package org.tambov.lanconnect;

import org.json.JSONObject;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

public final class Api {
    public final String base, token;
    public Api(String address, String bearer) throws Exception {
        URI uri = new URI(address.trim());
        if (!("http".equals(uri.getScheme()) || "https".equals(uri.getScheme())) || uri.getHost() == null ||
                uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null ||
                !(uri.getPath().isEmpty() || "/".equals(uri.getPath()))) {
            throw new IOException("Укажите http://IP-сервера:8080 без дополнительных путей");
        }
        base = address.trim().replaceAll("/+$", "");
        token = bearer;
    }
    public static JSONObject obj(Object... pairs) throws Exception {
        JSONObject out = new JSONObject();
        for (int i = 0; i < pairs.length; i += 2) out.put((String)pairs[i], pairs[i+1]);
        return out;
    }
    private HttpURLConnection open(String path, String method, byte[] data, String type) throws Exception {
        HttpURLConnection conn = (HttpURLConnection)new URL(base + path).openConnection(Proxy.NO_PROXY);
        conn.setConnectTimeout(5000); conn.setReadTimeout(10000);
        conn.setInstanceFollowRedirects(false); // Never forward a bearer token to a redirect target.
        conn.setRequestMethod(method);
        if (!token.isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + token);
        try {
            if (data != null) {
                conn.setDoOutput(true); conn.setRequestProperty("Content-Type", type);
                conn.setFixedLengthStreamingMode(data.length);
                try (OutputStream stream = conn.getOutputStream()) { stream.write(data); }
            }
            int code = conn.getResponseCode();
            if (code < 200 || code >= 300) {
                String error = "HTTP " + code;
                InputStream stream = conn.getErrorStream();
                if (stream != null) try (InputStream input = stream) {
                    String body = new String(readBounded(input, 32768), StandardCharsets.UTF_8);
                    try { error = new JSONObject(body).optString("error", error); } catch (Exception ignored) {}
                }
                throw new IOException(error);
            }
            return conn;
        } catch (Exception error) { conn.disconnect(); throw error; }
    }
    public JSONObject json(String path, JSONObject data) throws Exception {
        byte[] bytes = data == null ? null : data.toString().getBytes(StandardCharsets.UTF_8);
        HttpURLConnection conn = open(path, data == null ? "GET" : "POST", bytes, "application/json");
        try (InputStream in = conn.getInputStream()) {
            return new JSONObject(new String(readBounded(in, 8*1024*1024), StandardCharsets.UTF_8));
        } finally { conn.disconnect(); }
    }
    public byte[] binary(String path, byte[] data, int maxBytes) throws Exception {
        HttpURLConnection conn = open(path, data == null ? "GET" : "POST", data, "application/octet-stream");
        try (InputStream in = conn.getInputStream()) { return readBounded(in, maxBytes); }
        finally { conn.disconnect(); }
    }
    public interface Progress { void update(long bytes); }
    public void download(long id, long size, String expected, OutputStream target, Progress progress) throws Exception {
        HttpURLConnection conn = open("/download?id=" + id, "GET", null, "application/octet-stream");
        try (InputStream in = conn.getInputStream()) {
            long total = 0;
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[1024*1024]; int count;
            while ((count = in.read(buffer)) != -1) {
                if (Thread.currentThread().isInterrupted()) throw new InterruptedIOException("Скачивание отменено");
                digest.update(buffer,0,count); total += count;
                if (total > size) throw new IOException("Сервер передал больше заявленного размера");
                target.write(buffer, 0, count); progress.update(total);
            }
            if (total != size) throw new IOException("Скачивание прервано");
            if(expected != null && !expected.isEmpty()) {
                StringBuilder actual=new StringBuilder();
                for(byte b:digest.digest())actual.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
                if(!actual.toString().equalsIgnoreCase(expected))throw new IOException("Контрольная сумма скачанного файла не совпала");
            }
        } finally { conn.disconnect(); }
    }
    private static byte[] readBounded(InputStream stream, int max) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192]; int count;
        while ((count = stream.read(buffer)) != -1) {
            if ((long)out.size() + count > max) throw new IOException("Ответ превышает допустимый размер");
            out.write(buffer, 0, count);
        }
        return out.toByteArray();
    }
}
