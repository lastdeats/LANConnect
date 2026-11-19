package org.tambov.lanconnect;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.*;
import android.net.Uri;
import android.os.*;
import android.provider.OpenableColumns;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

public final class MainActivity extends Activity {
    private static final int BG=0xff091526, PANEL=0xff10243b, FG=0xffd9edf9, CYAN=0xff38cce8;
    private static final long MAX_FILE=5L*1024*1024*1024;
    private final Handler ui=new Handler(Looper.getMainLooper());
    private final ExecutorService jobs=Executors.newFixedThreadPool(3);
    private final ScheduledExecutorService polling=Executors.newSingleThreadScheduledExecutor();
    private final AtomicBoolean transferBusy=new AtomicBoolean(false), signing=new AtomicBoolean(false);
    private volatile boolean foreground=false, destroyed=false;
    private volatile Api api;
    private String username="";
    private long me=0;
    private LinearLayout root, conversation;
    private EditText address, name, password, composer;
    private TextView status;
    private Spinner contacts;
    private JSONObject state=new JSONObject();
    private final ArrayList<Contact> people=new ArrayList<>();
    private Contact selected;
    private String contactKey="", chatKey="";
    private JSONObject uploadTarget, pendingDownload;
    private Runnable afterMicPermission,resumeAfterPermission;
    private boolean requestingMic=false,startingLive=false;
    private long micRequestedAt=0;
    private final ConsentGuard consent=new ConsentGuard();
    private final Set<String> asked=new HashSet<>(), ended=new HashSet<>(), pendingCalls=new HashSet<>();
    private volatile String liveId="";
    private AudioCall audio;
    private Dialog liveDialog;
    private volatile boolean screenStopped=true;

    private static final class Contact {
        final long id; final boolean group; final String name;
        Contact(long id,boolean group,String name) { this.id=id;this.group=group;this.name=name; }
        String key() { return (group?"g":"u")+id; }
        public String toString() { return (group?"Группа: ":"")+name+" ["+id+"]"; }
    }
    private interface Task { void run() throws Exception; }
    private void post(Runnable action) { ui.post(()->{ if (!destroyed) action.run(); }); }
    private void run(Task task) { jobs.submit(()->{ try {task.run();} catch(Exception e){message(e.getMessage());} }); }
    private void message(String text) { post(()->status.setText(text==null?"Ошибка":text)); }
    private int dp(int value) { return Math.round(value*getResources().getDisplayMetrics().density); }
    private TextView label(String value,int size) {
        TextView out=new TextView(this);out.setText(value);out.setTextSize(size);out.setTextColor(FG);out.setPadding(dp(4),dp(6),dp(4),dp(6));return out;
    }
    private EditText field(String hint) {
        EditText out=new EditText(this);out.setTextColor(FG);out.setHintTextColor(0xff819bb5);out.setHint(hint);out.setSingleLine(true);return out;
    }
    private Button button(String text,Runnable click) {
        Button out=new Button(this);out.setText(text);out.setTextColor(CYAN);out.setAllCaps(false);out.setOnClickListener(v->click.run());return out;
    }
    private LinearLayout row() { LinearLayout out=new LinearLayout(this);out.setOrientation(LinearLayout.HORIZONTAL);return out; }
    private void base() {
        root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setPadding(dp(12),dp(8),dp(12),dp(8));root.setBackgroundColor(BG);
        // Keep interactive elements outside the system bars, including edge-to-edge on Android 15+.
        root.setOnApplyWindowInsetsListener((view,insets)->{
            view.setPadding(dp(12)+insets.getSystemWindowInsetLeft(),dp(8)+insets.getSystemWindowInsetTop(),
                    dp(12)+insets.getSystemWindowInsetRight(),dp(8)+insets.getSystemWindowInsetBottom());return insets;
        });
        setContentView(root);
        LinearLayout header=row();header.addView(label("LAN Connect • TAMBOV",21),new LinearLayout.LayoutParams(0,-2,1));
        Button info=button("ⓘ",this::deviceInfo);info.setMinWidth(0);info.setMinimumWidth(0);info.setContentDescription("Информация об устройстве");
        header.addView(info,new LinearLayout.LayoutParams(dp(48),dp(48)));root.addView(header);
        status=label("Локальная связь без Интернета",12);root.addView(status);
    }
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);login();
        polling.scheduleWithFixedDelay(()->{
            Api current=api;
            if (current==null || !foreground || destroyed) return;
            try {
                long snapshotStarted=SystemClock.elapsedRealtime();
                JSONObject next=current.json("/state?message_limit=100&file_limit=100",null);
                JSONArray sessions=current.json("/live/sessions",null).getJSONArray("sessions");
                post(()->{ if (api==current && foreground) { applyState(next);applySessions(sessions,snapshotStarted); } });
            } catch(Exception error) {
                message("Нет связи с сервером: "+error.getMessage());
                post(()->{ if (!liveId.isEmpty()) stopLive(true); });
            }
        },0,2,TimeUnit.SECONDS);
    }
    @Override protected void onResume() {
        super.onResume();foreground=true;
        Runnable action=resumeAfterPermission;resumeAfterPermission=null;
        if(action!=null){
            if(SystemClock.elapsedRealtime()-micRequestedAt<=60000)action.run();
            else message("Разрешение выдано. Нажмите «Звонок» повторно.");
        }
    }
    @Override protected void onStop() {
        foreground=false;
        if (!liveId.isEmpty() || !pendingCalls.isEmpty() || !consent.empty() || startingLive) stopLive(true);
        super.onStop();
    }
    @Override public void onDestroy() {
        destroyed=true;afterMicPermission=null;resumeAfterPermission=null;polling.shutdownNow();jobs.shutdownNow();super.onDestroy();
    }
    private void login() {
        base();
        ImageView icon=new ImageView(this);icon.setImageResource(org.tambov.lanconnect.R.drawable.hive);
        root.addView(icon,new LinearLayout.LayoutParams(dp(88),dp(88)));
        address=field("http://IP-сервера:8080");name=field("Имя пользователя");password=field("Пароль от 8 символов");
        address.setText(getPreferences(0).getString("server",""));
        name.setText(getPreferences(0).getString("name",""));
        password.setInputType(0x81);
        root.addView(address);root.addView(name);root.addView(password);
        root.addView(button("Войти",()->sign(false)));root.addView(button("Зарегистрироваться",()->sign(true)));
        root.addView(label("Укажите настоящий IP Windows-сервера.\nHTTP пока не защищён шифрованием.",13));
    }
    private void sign(boolean register) {
        if(!signing.compareAndSet(false,true))return;
        String server=address.getText().toString(), user=name.getText().toString().trim(), pass=password.getText().toString();
        run(()->{
            try {
            Api guest=new Api(server,"");JSONObject result=guest.json(register?"/register":"/login",Api.obj("name",user,"password",pass));
            Api connected=new Api(server,result.getString("token"));
            post(()->{
                if (!foreground) return;
                api=connected;username=user;password.setText("");
                getPreferences(0).edit().putString("server",connected.base).putString("name",user).apply();
                chatUI();message("Подключено к "+connected.base);
            });
            } finally { signing.set(false); }
        });
    }
    private void chatUI() {
        base();
        contacts=new Spinner(this);root.addView(contacts);
        contacts.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            public void onNothingSelected(android.widget.AdapterView<?> parent) {}
            public void onItemSelected(android.widget.AdapterView<?> parent,View view,int position,long id) {
                selected=position>0 && position<=people.size()?people.get(position-1):null;chatKey="";renderChat();
            }
        });
        LinearLayout groupbar=row();groupbar.addView(button("Создать группу",MainActivity.this::createGroup));
        groupbar.addView(button("Пригласить",MainActivity.this::invite));groupbar.addView(button("История",this::openHistory));
        for(int i=0;i<groupbar.getChildCount();i++){
            Button control=(Button)groupbar.getChildAt(i);control.setMinWidth(0);control.setMinimumWidth(0);control.setTextSize(13);
            control.setLayoutParams(new LinearLayout.LayoutParams(0,-2,1));
        }
        root.addView(groupbar);
        ScrollView scroll=new ScrollView(this);conversation=new LinearLayout(this);conversation.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(conversation);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        LinearLayout send=row();composer=field("Сообщение");send.addView(composer,new LinearLayout.LayoutParams(0,-2,1));
        send.addView(button("➤",this::sendMessage));send.addView(button("Файл",this::chooseUpload));root.addView(send);
        LinearLayout buttons=row();
        buttons.addView(button("Звонок",this::requestAudioCall));
        buttons.addView(button("Экран ПК",()->startLive("screen","view")));
        buttons.addView(button("Управление",()->startLive("screen","control")));
        for(int i=0;i<buttons.getChildCount();i++){
            Button control=(Button)buttons.getChildAt(i);control.setMinWidth(0);control.setMinimumWidth(0);control.setTextSize(13);
            control.setLayoutParams(new LinearLayout.LayoutParams(0,-2,1));
        }
        root.addView(buttons);root.addView(button("Остановить сеанс / отменить запрос",()->stopLive(true)));
    }
    private void deviceInfo() {
        String details="Производитель: "+Build.MANUFACTURER+"\nМодель: "+Build.MODEL+
                "\nAndroid: "+Build.VERSION.RELEASE+" (API "+Build.VERSION.SDK_INT+")"+
                "\nАрхитектуры: "+String.join(", ",Build.SUPPORTED_ABIS)+
                "\nОкно: "+root.getWidth()+" × "+root.getHeight()+" px"+
                "\nМикрофон: "+(getPackageManager().hasSystemFeature(PackageManager.FEATURE_MICROPHONE)?"есть":"не обнаружен")+
                "\nРазрешение микрофона: "+(checkSelfPermission(Manifest.permission.RECORD_AUDIO)==PackageManager.PERMISSION_GRANTED?"выдано":"не выдано");
        new AlertDialog.Builder(this).setTitle("LAN Connect — устройство").setMessage(details)
                .setNegativeButton("Закрыть",null).setPositiveButton("Скопировать",(d,w)->{
                    android.content.ClipboardManager clipboard=(android.content.ClipboardManager)getSystemService(Context.CLIPBOARD_SERVICE);
                    if(clipboard!=null)clipboard.setPrimaryClip(ClipData.newPlainText("LAN Connect — устройство",details));
                }).show();
    }
    private JSONObject target() throws Exception {
        if(selected==null)throw new IOException("Выберите чат");
        return Api.obj(selected.group?"gid":"target",selected.id);
    }
    private void applyState(JSONObject next) {
        state=next;me=next.optLong("me");
        try {
            ArrayList<Contact> updated=new ArrayList<>();JSONArray users=next.getJSONArray("users"),groups=next.getJSONArray("groups");
            for(int i=0;i<users.length();i++){JSONObject u=users.getJSONObject(i);if(u.getLong("id")!=me)updated.add(new Contact(u.getLong("id"),false,u.getString("name")));}
            for(int i=0;i<groups.length();i++){JSONObject g=groups.getJSONObject(i);updated.add(new Contact(g.getLong("id"),true,g.getString("name")));}
            String key=updated.toString();
            if(!key.equals(contactKey)) {
                String old=selected==null?"":selected.key();people.clear();people.addAll(updated);contactKey=key;
                ArrayList<String> labels=new ArrayList<>();labels.add("Выберите чат");int index=0;
                for(int i=0;i<people.size();i++){Contact c=people.get(i);labels.add(c.toString());if(c.key().equals(old))index=i+1;}
                ArrayAdapter<String> adapter=new ArrayAdapter<>(this,android.R.layout.simple_spinner_item,labels);
                adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);contacts.setAdapter(adapter);contacts.setSelection(index);
            }
            renderChat();
        } catch(Exception error){message(error.getMessage());}
    }
    private static int compareTimeline(JSONObject a,JSONObject b) {
        int order=Long.compare(a.optLong("created"),b.optLong("created"));
        if(order!=0)return order;
        order=Long.compare(a.optLong("id"),b.optLong("id"));
        if(order!=0)return order;
        return (a.has("body")?"message":"file").compareTo(b.has("body")?"message":"file");
    }
    private boolean inChat(JSONObject item) {
        if(selected==null)return false;
        if(selected.group)return item.optLong("gid")==selected.id;
        return item.isNull("gid") && ((item.optLong("sender")==me && item.optLong("target")==selected.id) ||
                (item.optLong("sender")==selected.id && item.optLong("target")==me));
    }
    private void renderChat() {
        if(conversation==null)return;
        try {
            JSONArray messages=state.optJSONArray("messages"),files=state.optJSONArray("files");
            if(messages==null || files==null)return;
            String key=(selected==null?"":selected.key())+messages.toString()+files.toString();
            if(key.equals(chatKey))return;chatKey=key;conversation.removeAllViews();
            if(selected==null){conversation.addView(label("Выберите человека или группу",16));return;}
            ArrayList<JSONObject> all=new ArrayList<>();
            for(int i=0;i<messages.length();i++){JSONObject item=messages.getJSONObject(i);if(inChat(item))all.add(item);}
            for(int i=0;i<files.length();i++){JSONObject item=files.getJSONObject(i);if(inChat(item))all.add(item);}
            all.sort(MainActivity::compareTimeline);
            if(all.isEmpty())conversation.addView(label("Свежих сообщений нет. Старую переписку можно открыть кнопкой «История».",15));
            for(JSONObject item:all) {
                if(item.has("body"))conversation.addView(label(item.optString("sender_name")+": "+item.optString("body"),16));
                else {
                    boolean expired=item.optInt("expired")!=0 || item.optLong("expires")<=System.currentTimeMillis()/1000;
                    String text="📎 "+item.optString("filename")+" • "+item.optLong("size")+" байт";
                    Button download=button(text+(expired?" • срок истёк":""),()->chooseDownload(item));download.setEnabled(!expired);conversation.addView(download);
                }
            }
        }catch(Exception error){message(error.getMessage());}
    }
    private void openHistory() {
        final JSONObject destination;
        try { destination=target(); } catch(Exception error){message(error.getMessage());return;}
        final Api current=api;
        Dialog dialog=new Dialog(this);
        LinearLayout layout=new LinearLayout(this);layout.setOrientation(LinearLayout.VERTICAL);layout.setBackgroundColor(BG);
        layout.setPadding(dp(10),dp(8),dp(10),dp(8));layout.addView(label("История: "+selected.name,19));
        TextView notice=label("Загрузка…",13);layout.addView(notice);
        ScrollView scroll=new ScrollView(this);LinearLayout body=new LinearLayout(this);body.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(body);layout.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        LinearLayout controls=row();layout.addView(controls);
        Button newer=button("Новее",()->{}),older=button("Раньше",()->{}),latest=button("Последние",()->{});
        controls.addView(newer);controls.addView(older);controls.addView(latest);
        layout.addView(button("Закрыть",dialog::dismiss));
        class Browser {
            boolean busy=false;
            String cursor="", next="";
            boolean hasMore=false;
            ArrayList<String> stack=new ArrayList<>();
            void buttons() {
                newer.setEnabled(!busy && !stack.isEmpty());
                older.setEnabled(!busy && hasMore);
                latest.setEnabled(!busy);
            }
            void fetch(String page,ArrayList<String> history) {
                if(busy)return;busy=true;buttons();notice.setText("Загрузка…");
                String scope=destination.has("gid")?"gid="+destination.optLong("gid"):"target="+destination.optLong("target");
                run(()->{
                    try {
                        JSONObject result=current.json("/history?"+scope+"&before="+java.net.URLEncoder.encode(page,"UTF-8"),null);
                        post(()->{
                            if(!dialog.isShowing())return;
                            busy=false;cursor=page;stack=history;next=result.optString("before");hasMore=result.optBoolean("has_more");
                            body.removeAllViews();
                            ArrayList<JSONObject> records=new ArrayList<>();
                            JSONArray messages=result.optJSONArray("messages"),files=result.optJSONArray("files");
                            if(messages!=null)for(int i=0;i<messages.length();i++)records.add(messages.optJSONObject(i));
                            if(files!=null)for(int i=0;i<files.length();i++)records.add(files.optJSONObject(i));
                            records.sort(MainActivity::compareTimeline);
                            for(JSONObject item:records){
                                if(item.has("body"))body.addView(label(item.optString("sender_name")+": "+item.optString("body"),16));
                                else{
                                    boolean expired=item.optInt("expired")!=0 || item.optLong("expires")<=System.currentTimeMillis()/1000;
                                    Button file=button("📎 "+item.optString("filename")+" • "+item.optLong("size")+" байт"+(expired?" • срок истёк":""),()->chooseDownload(item));
                                    file.setEnabled(!expired);body.addView(file);
                                }
                            }
                            if(records.isEmpty())body.addView(label("Сообщений нет",16));
                            notice.setText("Страница "+(stack.size()+1));buttons();scroll.post(()->scroll.scrollTo(0,0));
                        });
                    } catch(Exception error) {
                        post(()->{if(dialog.isShowing()){busy=false;notice.setText("Ошибка: "+error.getMessage());buttons();}});
                    }
                });
            }
            void older(){if(!busy && hasMore){ArrayList<String> h=new ArrayList<>(stack);h.add(cursor);fetch(next,h);}}
            void newer(){if(!busy && !stack.isEmpty()){ArrayList<String> h=new ArrayList<>(stack);String p=h.remove(h.size()-1);fetch(p,h);}}
        }
        Browser browser=new Browser();
        newer.setOnClickListener(v->browser.newer());older.setOnClickListener(v->browser.older());
        latest.setOnClickListener(v->browser.fetch("",new ArrayList<>()));
        dialog.setContentView(layout);dialog.show();if(dialog.getWindow()!=null)dialog.getWindow().setLayout(-1,-1);
        browser.fetch("",new ArrayList<>());
    }
    private void sendMessage() {
        try {
            JSONObject payload=target();String text=composer.getText().toString().trim();if(text.isEmpty())return;
            payload.put("body",text);Api current=api;composer.setText("");
            run(()->{current.json("/message",payload);message("Сообщение отправлено");});
        }catch(Exception error){message(error.getMessage());}
    }
    private void askText(String title,String hint,java.util.function.Consumer<String> done) {
        EditText input=field(hint);
        new AlertDialog.Builder(this).setTitle(title).setView(input).setNegativeButton("Отмена",null)
            .setPositiveButton("OK",(d,w)->done.accept(input.getText().toString().trim())).show();
    }
    private void createGroup() {
        askText("Создать группу","Название",value->run(()->{api.json("/group",Api.obj("name",value));message("Группа создана");}));
    }
    private void invite() {
        Contact chat=selected;
        if(chat==null || !chat.group){message("Выберите свою группу");return;}
        askText("Пригласить в "+chat.name,"ID пользователя из списка",value->run(()->{
            api.json("/invite",Api.obj("gid",chat.id,"uid",Long.parseLong(value)));message("Пользователь добавлен");
        }));
    }
    private void chooseUpload() {
        if(transferBusy.get()){message("Дождитесь завершения передачи");return;}
        try{uploadTarget=target();}catch(Exception error){message(error.getMessage());return;}
        Intent intent=new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("*/*").addCategory(Intent.CATEGORY_OPENABLE);
        intent.putExtra(Intent.EXTRA_LOCAL_ONLY,true);
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION|Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        try{startActivityForResult(intent,11);}
        catch(ActivityNotFoundException missing){
            Intent fallback=new Intent(Intent.ACTION_GET_CONTENT).setType("*/*").addCategory(Intent.CATEGORY_OPENABLE);
            fallback.putExtra(Intent.EXTRA_LOCAL_ONLY,true);fallback.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            try{startActivityForResult(fallback,11);}catch(RuntimeException error){message("Не найдено приложение выбора файлов. Включите системное приложение «Файлы».");}
        }catch(RuntimeException error){message("Не удалось открыть выбор файла: "+error.getMessage());}
    }
    private void chooseDownload(JSONObject file) {
        if(transferBusy.get()){message("Дождитесь завершения передачи");return;}
        pendingDownload=file;
        Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("application/octet-stream");
        intent.putExtra(Intent.EXTRA_TITLE,file.optString("filename","file"));
        try{startActivityForResult(intent,12);}catch(RuntimeException error){message("Не удалось выбрать место сохранения. Включите системное приложение «Файлы».");}
    }
    @Override public void onActivityResult(int request,int result,Intent data) {
        super.onActivityResult(request,result,data);
        if(result!=RESULT_OK || data==null)return;
        Uri selectedUri=data.getData();
        if(selectedUri==null && data.getClipData()!=null && data.getClipData().getItemCount()==1)selectedUri=data.getClipData().getItemAt(0).getUri();
        if(selectedUri==null){message("Выберите один файл");return;}
        final Uri uri=selectedUri;
        if(request==11){
            try{getContentResolver().takePersistableUriPermission(uri,data.getFlags()&Intent.FLAG_GRANT_READ_URI_PERMISSION);}catch(Exception ignored){}
            JSONObject dest=uploadTarget;
            if(dest!=null && transferBusy.compareAndSet(false,true))run(()->{try{upload(uri,dest);}finally{transferBusy.set(false);}});
        }else if(request==12){
            JSONObject file=pendingDownload;Api current=api;
            if(file!=null && transferBusy.compareAndSet(false,true))run(()->{
                try(OutputStream out=getContentResolver().openOutputStream(uri,"wt")){
                    if(out==null)throw new IOException("Не удалось открыть файл назначения");
                    long size=file.getLong("size");long[] last={0};
                    current.download(file.getLong("id"),size,file.isNull("sha256")?null:file.optString("sha256"),out,bytes->{long now=System.currentTimeMillis();if(now-last[0]>500){last[0]=now;message("Скачивание "+percent(bytes,size)+"%");}});
                    message("Файл сохранён");
                }catch(Exception error){
                    message("Скачивание не завершено: "+error.getMessage()+". Удалите неполный файл или повторите с заменой.");
                }finally{transferBusy.set(false);}
            });
        }
    }
    private static long percent(long n,long size){return size==0?100:n*100/size;}
    private static String hex(byte[] bytes){StringBuilder out=new StringBuilder();for(byte b:bytes)out.append(String.format(java.util.Locale.ROOT,"%02x",b&255));return out.toString();}
    private void upload(Uri uri,JSONObject dest) throws Exception {
        Api current=api;String filename="file";long size=-1;
        try(Cursor cursor=getContentResolver().query(uri,null,null,null,null)){
            if(cursor!=null && cursor.moveToFirst()){
                int title=cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME),length=cursor.getColumnIndex(OpenableColumns.SIZE);
                if(title>=0 && !cursor.isNull(title))filename=cursor.getString(title);
                if(length>=0 && !cursor.isNull(length))size=cursor.getLong(length);
            }
        }catch(Exception ignored){/* Some providers expose streams without metadata columns. */}
        if(size<0){
            try(android.content.res.AssetFileDescriptor descriptor=getContentResolver().openAssetFileDescriptor(uri,"r")){
                if(descriptor!=null)size=descriptor.getLength();
            }catch(Exception ignored){}
        }
        if(size<0){
            message("Определяю размер файла…");
            try(InputStream stream=getContentResolver().openInputStream(uri)){
                if(stream==null)throw new IOException("Файл недоступен");
                size=BoundedStream.measure(stream,MAX_FILE);
            }
        }
        if(size<0 || size>MAX_FILE)throw new IOException("Максимальный размер одного файла — 5 ГиБ");
        String marker="upload-"+hex(MessageDigest.getInstance("SHA-256").digest((current.base+"\n"+username+"\n"+uri+"\n"+dest).getBytes(StandardCharsets.UTF_8)));
        String saved=getPreferences(0).getString(marker,"");String id=null;long offset=0;
        if(!saved.isEmpty()){
            JSONObject meta=new JSONObject(saved);
            if(meta.optLong("size")==size){
                try{JSONObject status=current.json("/upload/status",Api.obj("upload_id",meta.getString("id")));id=meta.getString("id");offset=status.getLong("offset");}
                catch(Exception ignored){}
            }
        }
        if(id==null){
            JSONObject params=new JSONObject(dest.toString());params.put("size",size);params.put("name",filename);
            JSONObject start=current.json("/upload/start",params);id=start.getString("upload_id");
            getPreferences(0).edit().putString(marker,Api.obj("id",id,"size",size).toString()).commit();
        }
        if(offset<0 || offset>size)throw new IOException("Неверная позиция на сервере");
        MessageDigest digest=MessageDigest.getInstance("SHA-256");long total=0;byte[] buffer=new byte[1024*1024];
        try(InputStream in=getContentResolver().openInputStream(uri)){
            if(in==null)throw new IOException("Файл недоступен");
            while(true){
                if(Thread.currentThread().isInterrupted())throw new InterruptedIOException("Отправка отменена");
                int used=BoundedStream.readChunk(in,buffer);
                if(used==0)break;
                digest.update(buffer,0,used);
                if(total+used>size)throw new IOException("Размер исходного файла изменился");
                if(total+used>offset){
                    int skip=(int)Math.max(0,offset-total);byte[] part=Arrays.copyOfRange(buffer,skip,used);
                    JSONObject result=new JSONObject(new String(current.binary("/upload/chunk?id="+id+"&offset="+offset,part,32768),StandardCharsets.UTF_8));
                    offset=result.getLong("offset");message("Отправка "+percent(offset,size)+"%");
                }
                total+=used;
            }
        }
        if(total!=size)throw new IOException("Исходный файл прочитан не полностью");
        current.json("/upload/finish",Api.obj("upload_id",id,"sha256",hex(digest.digest())));
        getPreferences(0).edit().remove(marker).apply();message("Файл отправлен");
    }
    private void requestAudioCall(){
        Contact person=selected;long ticket=consent.ticket();
        if(person==null || person.group){message("Выберите человека");return;}
        withMicrophone(()->startLiveFor(person,"audio","view",ticket));
    }
    private void withMicrophone(Runnable action) {
        if(!getPackageManager().hasSystemFeature(PackageManager.FEATURE_MICROPHONE)){message("На устройстве не найден микрофон");return;}
        if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)==PackageManager.PERMISSION_GRANTED){action.run();return;}
        if(requestingMic){message("Ответьте на запрос доступа к микрофону");return;}
        long ticket=consent.ticket();micRequestedAt=SystemClock.elapsedRealtime();requestingMic=true;
        afterMicPermission=()->{if(consent.current(ticket))action.run();};
        try{requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO},21);}
        catch(RuntimeException error){requestingMic=false;afterMicPermission=null;message("Не удалось запросить микрофон: "+error.getMessage());}
    }
    @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] grants){
        super.onRequestPermissionsResult(request,permissions,grants);
        if(request==21){Runnable action=afterMicPermission;afterMicPermission=null;requestingMic=false;
            if(grants.length>0 && grants[0]==PackageManager.PERMISSION_GRANTED){
                if(action==null || SystemClock.elapsedRealtime()-micRequestedAt>60000)message("Разрешение выдано. Нажмите «Звонок» повторно.");
                else if(foreground)action.run();else resumeAfterPermission=action;
            }
            else {
                message("Для аудиозвонка нужен доступ к микрофону");
                if(foreground && !shouldShowRequestPermissionRationale(Manifest.permission.RECORD_AUDIO)){
                    new AlertDialog.Builder(this).setTitle("Разрешение микрофона")
                        .setMessage("Разрешите микрофон в настройках LAN Connect, если хотите пользоваться звонками.")
                        .setNegativeButton("Позже",null).setPositiveButton("Настройки",(d,w)->{
                            try{startActivity(new Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,Uri.parse("package:"+getPackageName())));}
                            catch(RuntimeException error){message("Откройте настройки приложения через настройки телефона");}
                        }).show();
                }
            }}
    }
    private void startLive(String kind,String mode) {startLiveFor(selected,kind,mode,consent.ticket());}
    private void startLiveFor(Contact person,String kind,String mode,long ticket) {
        if(!consent.current(ticket))return;
        if(startingLive){message("Предыдущий запрос ещё завершается. Повторите через несколько секунд.");return;}
        if(!liveId.isEmpty() || !pendingCalls.isEmpty() || !consent.empty()){message("Сначала завершите текущий сеанс");return;}
        if(person==null || person.group){message("Выберите человека");return;}
        startingLive=true;
        run(()->{
            try {
            String id=api.json("/live/start",Api.obj("owner",person.id,"kind",kind,"mode",mode)).getString("id");
            post(()->{
                startingLive=false;
                if(foreground && consent.allow(ticket,id,SystemClock.elapsedRealtime())){pendingCalls.add(id);message("Запрос отправлен. Ожидается согласие. Кнопка Стоп отменяет запрос.");}
                else{ended.add(id);run(()->api.json("/live/end",Api.obj("id",id)));}
            });
            } catch(Exception error){post(()->startingLive=false);throw error;}
        });
    }
    private void acceptAudio(String id,long ticket){
        if(!foreground || !consent.allow(ticket,id,SystemClock.elapsedRealtime()))return;
        run(()->api.json("/live/decision",Api.obj("id",id,"approve",true)));
    }
    private void applySessions(JSONArray sessions,long snapshotStarted) {
        boolean present=liveId.isEmpty();
        Set<String> sessionIds=new HashSet<>();
        for(int i=0;i<sessions.length();i++){
            JSONObject s=sessions.optJSONObject(i);if(s==null)continue;
            String id=s.optString("id"),kind=s.optString("kind");sessionIds.add(id);
            if(id.equals(liveId))present=true;
            if(ended.contains(id))continue;
            if("pending".equals(s.optString("status")) && s.optLong("owner")==me && asked.add(id)){
                if(!"audio".equals(kind)){
                    // No Android screen capture or Accessibility service is enabled by this client.
                    run(()->api.json("/live/decision",Api.obj("id",id,"approve",false)));
                    message("Передача экрана Android ещё не реализована");continue;
                }
                if(!liveId.isEmpty() || !pendingCalls.isEmpty() || !consent.empty() || startingLive){
                    run(()->api.json("/live/decision",Api.obj("id",id,"approve",false)));continue;
                }
                final long ticket=consent.ticket();
                new AlertDialog.Builder(this).setTitle("Входящий аудиозвонок")
                    .setMessage(userName(s.optLong("requester"))+". Принять и включить микрофон?")
                    .setNegativeButton("Отклонить",(d,w)->run(()->api.json("/live/decision",Api.obj("id",id,"approve",false))))
                    .setPositiveButton("Принять",(d,w)->withMicrophone(()->acceptAudio(id,ticket)))
                    .setOnCancelListener(d->run(()->api.json("/live/decision",Api.obj("id",id,"approve",false)))).show();
            }else if("approved".equals(s.optString("status")) && liveId.isEmpty() && consent.allowed(id))activate(s);
        }
        consent.reconcile(sessionIds,snapshotStarted);pendingCalls.removeIf(id->!consent.allowed(id));
        if(!present && !liveId.isEmpty())stopLive(false);
    }
    private String userName(long id){for(Contact c:people)if(!c.group && c.id==id)return c.name;return "ID "+id;}
    private void activate(JSONObject session) {
        if(!foreground)return;
        String id=session.optString("id"),kind=session.optString("kind");
        if("audio".equals(kind) && checkSelfPermission(Manifest.permission.RECORD_AUDIO)!=PackageManager.PERMISSION_GRANTED){
            ended.add(id);consent.remove(id);pendingCalls.remove(id);run(()->api.json("/live/end",Api.obj("id",id)));message("Нет разрешения микрофона");return;
        }
        pendingCalls.remove(id);liveId=id;screenStopped=false;
        liveDialog=new Dialog(this);LinearLayout layout=new LinearLayout(this);layout.setOrientation(LinearLayout.VERTICAL);layout.setBackgroundColor(BG);layout.setPadding(dp(8),dp(8),dp(8),dp(8));
        layout.addView(label("audio".equals(kind)?"Аудиозвонок • микрофон включён":"Экран Windows • "+session.optString("mode"),18));
        layout.addView(button("ОСТАНОВИТЬ",()->stopLive(true)));
        if("audio".equals(kind)){
            layout.addView(label("Используйте наушники.\nПри сворачивании приложения звонок завершится.",14));
            audio=new AudioCall(this,api,id,reason->post(()->{if(id.equals(liveId)){message(reason);stopLive(true);}}));audio.start();
        }else if(session.optLong("requester")==me){
            ImageView image=new ImageView(this);image.setScaleType(ImageView.ScaleType.FIT_CENTER);
            layout.addView(image,new LinearLayout.LayoutParams(-1,0,1));
            boolean control="control".equals(session.optString("mode"));
            if(control){
                image.setOnTouchListener((view,event)->{
                    if(event.getAction()==android.view.MotionEvent.ACTION_UP && image.getDrawable()!=null){
                        android.graphics.RectF bounds=new android.graphics.RectF(image.getDrawable().getBounds());image.getImageMatrix().mapRect(bounds);
                        float x=event.getX()-image.getPaddingLeft(),y=event.getY()-image.getPaddingTop();
                        if(bounds.contains(x,y)){final double nx=(x-bounds.left)/bounds.width(),ny=(y-bounds.top)/bounds.height();
                            run(()->api.json("/live/command",Api.obj("id",id,"command",Api.obj("action","click","x",nx,"y",ny,"button","left"))));}
                    }return true;
                });
                LinearLayout textrow=row();EditText input=field("Текст для ввода на ПК");textrow.addView(input,new LinearLayout.LayoutParams(0,-2,1));
                textrow.addView(button("Ввести",()->{String text=input.getText().toString();if(text.length()>0 && text.length()<=256){input.setText("");run(()->api.json("/live/command",Api.obj("id",id,"command",Api.obj("action","text","text",text))));}}));layout.addView(textrow);
                LinearLayout keys=row();for(String key:new String[]{"Return","BackSpace","Escape"})keys.addView(button(key,()->run(()->api.json("/live/command",Api.obj("id",id,"command",Api.obj("action","key","key",key))))));layout.addView(keys);
            }
            viewScreen(id,image);
        }else{stopLive(true);return;}
        liveDialog.setContentView(layout);liveDialog.setOnCancelListener(d->stopLive(true));liveDialog.show();
        if(liveDialog.getWindow()!=null)liveDialog.getWindow().setLayout(-1,"screen".equals(kind)?-1:-2);
    }
    private void viewScreen(String id,ImageView view){
        Api current=api;
        new Thread(()->{
            try{
                while(!screenStopped && !destroyed && id.equals(liveId)){
                    byte[] raw=current.binary("/live/frame?id="+id,null,2*1024*1024);
                    if(raw.length>0){
                        BitmapFactory.Options opts=new BitmapFactory.Options();opts.inJustDecodeBounds=true;BitmapFactory.decodeByteArray(raw,0,raw.length,opts);
                        if(opts.outWidth<=0 || opts.outHeight<=0 || opts.outWidth>4096 || opts.outHeight>4096)throw new IOException("Неверный кадр");
                        BitmapFactory.Options decode=new BitmapFactory.Options();decode.inSampleSize=ImageSizing.sample(opts.outWidth,opts.outHeight);decode.inPreferredConfig=Bitmap.Config.RGB_565;
                        Bitmap bitmap=BitmapFactory.decodeByteArray(raw,0,raw.length,decode);
                        if(bitmap==null)throw new IOException("JPEG не читается");
                        post(()->{if(!screenStopped && id.equals(liveId))view.setImageBitmap(bitmap);});
                    }
                    Thread.sleep(250);
                }
            }catch(OutOfMemoryError error){message("Недостаточно памяти для просмотра экрана");post(()->{if(id.equals(liveId))stopLive(true);});}
            catch(Exception error){message("Просмотр прерван: "+error.getMessage());post(()->{if(id.equals(liveId))stopLive(true);});}
        },"LAN-screen-view").start();
    }
    private void stopLive(boolean notify){
        String id=liveId;liveId="";screenStopped=true;
        if(!id.isEmpty()){ended.add(id);consent.remove(id);}
        if(audio!=null){audio.stop();audio=null;}
        if(liveDialog!=null){liveDialog.setOnCancelListener(null);liveDialog.dismiss();liveDialog=null;}
        if(notify){
            Set<String> closing=consent.cancelAll();closing.addAll(pendingCalls);pendingCalls.clear();afterMicPermission=null;resumeAfterPermission=null;
            if(!id.isEmpty())closing.add(id);
            for(String closingId:closing){ended.add(closingId);run(()->api.json("/live/end",Api.obj("id",closingId)));}
        }
    }
}
