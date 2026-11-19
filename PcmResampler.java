package org.tambov.lanconnect;

import java.util.Arrays;

/** Streaming mono PCM16 converter. Holds phase across reads, independent of chunk sizes.
 * A 63-tap low-pass filter precedes downsampling. No Android or native-library dependency.
 */
public final class PcmResampler {
    private final int inputRate, outputRate;
    private long index=-1, next=0;
    private double previous;
    private final double[] filter, ring;
    private int head=0;
    public PcmResampler(int inputRate,int outputRate) {
        if(inputRate<8000 || inputRate>96000 || outputRate<8000 || outputRate>96000)
            throw new IllegalArgumentException("Unsupported sample rate");
        this.inputRate=inputRate;this.outputRate=outputRate;
        if(inputRate>outputRate){
            filter=new double[63];ring=new double[63];double cutoff=.45*outputRate/inputRate,sum=0;
            for(int i=0;i<filter.length;i++){
                int n=i-filter.length/2;
                double sinc=n==0?2*cutoff:Math.sin(2*Math.PI*cutoff*n)/(Math.PI*n);
                filter[i]=sinc*(.54-.46*Math.cos(2*Math.PI*i/(filter.length-1)));sum+=filter[i];
            }
            for(int i=0;i<filter.length;i++)filter[i]/=sum;
        }else{filter=null;ring=null;}
    }
    public short[] convert(short[] input,int count) {
        if(count<0 || count>input.length || count>96000)throw new IllegalArgumentException("Invalid block size");
        short[] output=new short[(int)((long)count*outputRate/inputRate)+16];int used=0;
        for(int i=0;i<count;i++){
            double value=input[i];
            if(filter!=null){
                if(index==-1)Arrays.fill(ring,value);
                ring[head]=value;value=0;
                for(int tap=0;tap<filter.length;tap++)value+=filter[tap]*ring[(head-tap+ring.length)%ring.length];
                head=(head+1)%ring.length;
            }
            index++;
            if(index==0)previous=value;
            while(next<=index*(long)outputRate){
                double fraction=index==0?1:(next-(index-1)*(long)outputRate)/(double)outputRate;
                double sample=previous+(value-previous)*fraction;
                if(used==output.length)output=Arrays.copyOf(output,output.length+32);
                output[used++]=(short)Math.max(-32768,Math.min(32767,Math.round(sample)));
                next+=inputRate;
            }
            previous=value;
        }
        return Arrays.copyOf(output,used);
    }
    public static byte[] encode(short[] samples) {
        byte[] raw=new byte[samples.length*2];
        for(int i=0;i<samples.length;i++){raw[i*2]=(byte)samples[i];raw[i*2+1]=(byte)(samples[i]>>8);}
        return raw;
    }
    public static short[] decode(byte[] bytes) {
        if(bytes.length%2!=0)throw new IllegalArgumentException("Odd PCM packet");
        short[] samples=new short[bytes.length/2];
        for(int i=0;i<samples.length;i++)samples[i]=(short)((bytes[i*2]&255)|((bytes[i*2+1]&255)<<8));
        return samples;
    }
}
