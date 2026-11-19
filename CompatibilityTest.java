import org.tambov.lanconnect.PcmResampler;
import org.tambov.lanconnect.BoundedStream;
import org.tambov.lanconnect.AudioSelector;
import org.tambov.lanconnect.ImageSizing;
import org.tambov.lanconnect.ConsentGuard;
import java.io.*;
import java.util.*;

public final class CompatibilityTest {
    static void check(boolean ok,String message){if(!ok)throw new AssertionError(message);}
    static short[] wave(int rate,int frequency){short[] a=new short[rate];for(int i=0;i<a.length;i++)a[i]=(short)Math.round(20000*Math.sin(2*Math.PI*frequency*i/rate));return a;}
    static double rms(short[] a){double sum=0;for(int i=100;i<a.length;i++)sum+=(double)a[i]*a[i];return Math.sqrt(sum/(a.length-100));}
    static void conversion(int from,int to){
        short[] input=wave(from,997);short[] full=new PcmResampler(from,to).convert(input,input.length);
        long expected=((long)input.length-1)*to/from+1;check(full.length==expected,"sample count "+from+" -> "+to);
        PcmResampler streaming=new PcmResampler(from,to);ByteArrayOutputStream packed=new ByteArrayOutputStream();Random random=new Random(7);
        for(int i=0;i<input.length;){int n=Math.min(input.length-i,1+random.nextInt(300));short[] part=Arrays.copyOfRange(input,i,i+n);byte[] raw=PcmResampler.encode(streaming.convert(part,n));packed.write(raw,0,raw.length);i+=n;}
        check(Arrays.equals(full,PcmResampler.decode(packed.toByteArray())),"chunk-boundary discontinuity");
        if(from==to)check(Arrays.equals(input,full),"identity conversion changed samples");
        short[] dc=new short[from];Arrays.fill(dc,(short)-32768);for(short sample:new PcmResampler(from,to).convert(dc,dc.length))check(sample==-32768,"DC/clipping");
    }
    static InputStream virtualFile(long bytes){
        return new InputStream(){long remaining=bytes;
            public int read(){if(remaining==0)return -1;remaining--;return 0;}
            public int read(byte[] b,int offset,int length){if(remaining==0)return -1;int n=(int)Math.min(remaining,length);remaining-=n;return n;}
        };
    }
    public static void main(String[] args)throws Exception{
        for(int[] pair:new int[][]{{16000,16000},{48000,16000},{44100,16000},{16000,44100},{16000,48000}})conversion(pair[0],pair[1]);
        short[] low=new PcmResampler(48000,16000).convert(wave(48000,1000),48000);
        short[] high=new PcmResampler(48000,16000).convert(wave(48000,14000),48000);
        check(rms(high)<rms(low)*.03,"downsampling alias rejection");
        short[] extremes={-32768,-1,0,1,32767};check(Arrays.equals(extremes,PcmResampler.decode(PcmResampler.encode(extremes))),"PCM endianness");
        try{PcmResampler.decode(new byte[3]);throw new AssertionError("odd packet accepted");}catch(IllegalArgumentException expected){}
        check(BoundedStream.measure(new ByteArrayInputStream(new byte[0]),0)==0,"empty file");
        check(BoundedStream.measure(new ByteArrayInputStream(new byte[130000]),130000)==130000,"file boundary");
        try{BoundedStream.measure(new ByteArrayInputStream(new byte[130001]),130000);throw new AssertionError("oversize file accepted");}catch(IOException expected){}
        long fiveGiB=5L*1024*1024*1024;
        check(BoundedStream.measure(virtualFile(fiveGiB),fiveGiB)==fiveGiB,"5 GiB counter overflow");
        try{BoundedStream.measure(virtualFile(fiveGiB+1),fiveGiB);throw new AssertionError("5 GiB limit bypassed");}catch(IOException expected){}
        InputStream shortReads=new ByteArrayInputStream(new byte[70001]){public synchronized int read(byte[] b,int o,int n){return super.read(b,o,Math.min(3,n));}};
        check(BoundedStream.measure(shortReads,80000)==70001,"short provider reads");
        InputStream stalled=new InputStream(){public int read(){return 0;}public int read(byte[] b,int o,int n){return 0;}};
        try{BoundedStream.measure(stalled,100);throw new AssertionError("stalled source accepted");}catch(IOException expected){}
        Thread.currentThread().interrupt();
        try{BoundedStream.measure(new ByteArrayInputStream(new byte[1]),100);throw new AssertionError("interruption ignored");}catch(InterruptedIOException expected){}finally{Thread.interrupted();}
        ArrayList<String> attempts=new ArrayList<>();
        AudioSelector.Choice<String> choice=AudioSelector.choose(2,(rate,variant)->{
            attempts.add(variant+":"+rate);if(rate==44100 && variant==1)return "MIC";throw new IllegalStateException("unsupported");
        });
        check(choice.rate==44100 && choice.variant==1 && attempts.size()==6,"audio fallback");
        attempts.clear();
        try{AudioSelector.choose(2,(rate,variant)->{attempts.add("attempt");throw new SecurityException("denied");});throw new AssertionError("permission denial ignored");}
        catch(SecurityException expected){check(attempts.size()==1,"permission denial retried");}
        choice=AudioSelector.choose(1,(rate,variant)->"first");check(choice.rate==16000,"preferred format skipped");
        check(ImageSizing.sample(1280,720)==1,"normal frame reduced");
        check(ImageSizing.sample(4096,4096)==4,"large frame not bounded");
        for(int width:new int[]{1,720,1280,1281,2561,4096})for(int height:new int[]{1,720,1280,1281,2561,4096}){
            int scale=ImageSizing.sample(width,height);check((width+scale-1)/scale<=1280 && (height+scale-1)/scale<=1280,"frame exceeds memory budget");
        }
        try{ImageSizing.sample(4097,1);throw new AssertionError("oversize image accepted");}catch(IllegalArgumentException expected){}
        ConsentGuard guard=new ConsentGuard();long ticket=guard.ticket();
        check(!guard.allowed("foreign"),"unknown live session auto-approved");
        guard.cancelAll();check(!guard.allow(ticket,"late-response",5),"cancelled request revived");
        ticket=guard.ticket();check(guard.allow(ticket,"fresh",10),"new consent rejected");
        guard.reconcile(Collections.emptySet(),9);check(guard.allowed("fresh"),"old poll erased new consent");
        guard.reconcile(Collections.emptySet(),11);check(!guard.allowed("fresh"),"missing session retained");
        guard.allow(ticket,"active",12);check(guard.cancelAll().contains("active") && !guard.allowed("active"),"stop did not revoke consent");
        System.out.println("PASS: PCM rates/filter/continuity; bounded streams/frames; fallback and consent cancellation (JVM only, no Android hardware)");
    }
}
