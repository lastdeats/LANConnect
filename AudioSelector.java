package org.tambov.lanconnect;

/** Generic fallback policy; opening callbacks must release any failed native candidate. */
public final class AudioSelector {
    public interface Opener<T> { T open(int rate,int variant) throws Exception; }
    public static final class Choice<T> {
        public final int rate,variant;
        public final T device;
        Choice(int rate,int variant,T device){this.rate=rate;this.variant=variant;this.device=device;}
    }
    public static <T> Choice<T> choose(int variants,Opener<T> opener) throws Exception {
        if(variants<1 || variants>3)throw new IllegalArgumentException("Invalid audio variants");
        Exception last=null;
        for(int variant=0;variant<variants;variant++)for(int rate:new int[]{16000,48000,44100}){
            if(Thread.currentThread().isInterrupted())throw new InterruptedException("Audio setup cancelled");
            try{
                T device=opener.open(rate,variant);
                if(device==null)throw new IllegalStateException("No device");
                return new Choice<>(rate,variant,device);
            }catch(SecurityException denied){throw denied;}
            catch(InterruptedException cancelled){throw cancelled;}
            catch(Exception unsupported){last=unsupported;}
        }
        throw new IllegalStateException("Нет совместимого режима аудиоустройства",last);
    }
    private AudioSelector(){}
}
