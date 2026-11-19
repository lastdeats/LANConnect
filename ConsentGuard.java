package org.tambov.lanconnect;

import java.util.*;

/** UI-thread-confined consent state. Late network replies cannot undo a local stop. */
public final class ConsentGuard {
    private long generation=0;
    private final Map<String,Long> allowed=new HashMap<>();
    public long ticket(){return generation;}
    public boolean current(long ticket){return ticket==generation;}
    public boolean allow(long ticket,String id,long now){
        if(!current(ticket))return false;
        allowed.put(id,now);return true;
    }
    public boolean allowed(String id){return allowed.containsKey(id);}
    public boolean empty(){return allowed.isEmpty();}
    public void remove(String id){allowed.remove(id);}
    public void reconcile(Set<String> liveIds,long snapshotStarted){
        // A poll that started before allow() must not erase a just-created session.
        allowed.entrySet().removeIf(entry->entry.getValue()<snapshotStarted && !liveIds.contains(entry.getKey()));
    }
    public Set<String> cancelAll(){
        generation++;Set<String> closing=new HashSet<>(allowed.keySet());allowed.clear();return closing;
    }
}
