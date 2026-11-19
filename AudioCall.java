package org.tambov.lanconnect;

import android.content.Context;
import android.media.*;
import java.util.Arrays;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/** Foreground Activity only. Generic Android APIs; no manufacturer or Google dependencies. */
public final class AudioCall {
    private final Api api;
    private final String id;
    private final Consumer<String> onError;
    private final AudioManager manager;
    private final AtomicBoolean stopped=new AtomicBoolean(false);
    private final ArrayBlockingQueue<byte[]> packets=new ArrayBlockingQueue<>(4);
    private volatile AudioRecord recorder;
    private volatile AudioTrack player;
    private AudioFocusRequest focus;
    public AudioCall(Context context,Api api,String id,Consumer<String> onError) {
        this.api=api;this.id=id;this.onError=onError;
        manager=(AudioManager)context.getApplicationContext().getSystemService(Context.AUDIO_SERVICE);
    }
    public void start() {
        try{
            if(manager==null)throw new IllegalStateException("Звуковая служба недоступна");
            if(manager.isMicrophoneMute()){fail("Микрофон выключен в системе. Включите его в настройках телефона и повторите звонок.");return;}
            AudioAttributes attributes=new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build();
            focus=new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT).setAudioAttributes(attributes)
                    .setWillPauseWhenDucked(true).setOnAudioFocusChangeListener(change->{
                        if(change<0)fail("Звонок завершён: звук занят другим приложением");
                    }).build();
            if(manager.requestAudioFocus(focus)!=AudioManager.AUDIOFOCUS_REQUEST_GRANTED){
                fail("Система не разрешила аудиозвонок. Откройте приложение и завершите другой звонок.");return;
            }
            if(stopped.get())return;
            new Thread(this::capture,"LAN-mic").start();
            new Thread(this::send,"LAN-audio-send").start();
            new Thread(this::play,"LAN-audio-play").start();
        }catch(Exception error){fail("Не удалось начать аудиозвонок: "+error.getMessage());}
    }
    public void stop() {if(stopped.compareAndSet(false,true))stopDevices();}
    private void stopDevices() {
        AudioRecord input=recorder;
        if(input!=null)try{input.stop();}catch(Exception ignored){}
        AudioTrack output=player;
        if(output!=null)try{output.pause();output.flush();}catch(Exception ignored){}
        synchronized(this){
            if(focus!=null){try{manager.abandonAudioFocusRequest(focus);}catch(Exception ignored){}focus=null;}
        }
    }
    private void fail(String reason){
        if(stopped.compareAndSet(false,true)){stopDevices();onError.accept(reason);}
    }
    private void capture() {
        AudioRecord input=null;
        try{
            AudioSelector.Choice<AudioRecord> selected=AudioSelector.choose(2,(rate,variant)->{
                if(stopped.get())throw new InterruptedException("Audio setup cancelled");
                int min=AudioRecord.getMinBufferSize(rate,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT);
                if(min<=0)throw new IllegalStateException("Unsupported microphone rate");
                AudioRecord candidate=null;boolean ready=false;
                try{
                    int source=variant==0?MediaRecorder.AudioSource.VOICE_COMMUNICATION:MediaRecorder.AudioSource.MIC;
                    candidate=new AudioRecord(source,rate,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT,Math.max(min,rate*2/5));
                    if(candidate.getState()!=AudioRecord.STATE_INITIALIZED)throw new IllegalStateException("Microphone unavailable");
                    if(stopped.get())throw new InterruptedException("Audio setup cancelled");
                    candidate.startRecording();
                    if(candidate.getRecordingState()!=AudioRecord.RECORDSTATE_RECORDING)throw new IllegalStateException("Recording did not start");
                    ready=true;return candidate;
                }finally{if(!ready && candidate!=null)candidate.release();}
            });
            input=selected.device;recorder=input;
            if(stopped.get())return;
            PcmResampler converter=new PcmResampler(selected.rate,16000);
            short[] buffer=new short[selected.rate/10];byte[] packet=new byte[3200];int offset=0;
            while(!stopped.get()){
                int count=input.read(buffer,0,buffer.length);
                if(count<=0)throw new IllegalStateException("Микрофон отключён или занят");
                for(short sample:converter.convert(buffer,count)){
                    packet[offset++]=(byte)sample;packet[offset++]=(byte)(sample>>8);
                    if(offset==packet.length){
                        if(!stopped.get()){
                            byte[] raw=Arrays.copyOf(packet,packet.length);
                            if(!packets.offer(raw)){packets.poll();packets.offer(raw);}
                        }
                        offset=0;
                    }
                }
            }
        }catch(SecurityException denied){fail("Нет разрешения на микрофон. Разрешите его в настройках приложения.");}
        catch(Exception error){fail("Запись звука прервана: "+error.getMessage());}
        finally{recorder=null;if(input!=null)input.release();}
    }
    private void send() {
        long sequence=0;
        try{
            while(!stopped.get()){
                byte[] packet=packets.poll(100,TimeUnit.MILLISECONDS);
                if(packet!=null && !stopped.get()){
                    api.binary("/live/audio?id="+id+"&sequence="+sequence,packet,32768);sequence++;
                }
            }
        }catch(Exception error){fail("Отправка звука прервана: "+error.getMessage());}
    }
    private void play() {
        AudioTrack output=null;
        try{
            AudioSelector.Choice<AudioTrack> selected=AudioSelector.choose(1,(rate,variant)->{
                int min=AudioTrack.getMinBufferSize(rate,AudioFormat.CHANNEL_OUT_MONO,AudioFormat.ENCODING_PCM_16BIT);
                if(min<=0)throw new IllegalStateException("Unsupported speaker rate");
                AudioTrack candidate=null;boolean ready=false;
                try{
                    candidate=new AudioTrack.Builder().setAudioAttributes(new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                            .setAudioFormat(new AudioFormat.Builder().setSampleRate(rate).setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                            .setBufferSizeInBytes(Math.max(min,rate*2/5)).setTransferMode(AudioTrack.MODE_STREAM).build();
                    if(candidate.getState()!=AudioTrack.STATE_INITIALIZED)throw new IllegalStateException("Speaker unavailable");
                    ready=true;return candidate;
                }finally{if(!ready && candidate!=null)candidate.release();}
            });
            output=selected.device;player=output;
            if(stopped.get())return;
            output.play();PcmResampler converter=new PcmResampler(16000,selected.rate);
            while(!stopped.get()){
                byte[] raw=api.binary("/live/audio?id="+id,null,3200);
                short[] samples=converter.convert(PcmResampler.decode(raw),raw.length/2);int offset=0;
                while(offset<samples.length && !stopped.get()){
                    int n=output.write(samples,offset,samples.length-offset,AudioTrack.WRITE_NON_BLOCKING);
                    if(n<0)throw new IllegalStateException("Динамик отключён или занят");
                    if(n==0)Thread.sleep(5);else offset+=n;
                }
                if(raw.length==0)Thread.sleep(25);
            }
        }catch(Exception error){fail("Воспроизведение прервано: "+error.getMessage());}
        finally{player=null;if(output!=null)output.release();}
    }
}
