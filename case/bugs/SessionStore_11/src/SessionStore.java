import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;
import java.util.Map.Entry;

public final class SessionStore {
    private final Map<String, Session> sessions;

    public SessionStore() {
        this.sessions = new HashMap<>();
    }

    public void create(String id, long now, long maxInactiveMs, long maxLifetimeMs) {
        sessions.put(id, new Session(id, now, maxInactiveMs, maxLifetimeMs));
    }

    public Session lookup(String id) {
        return sessions.get(id);
    }

    public void touch(String id, long now) {
        Session s = sessions.get(id);
        if (s != null) {
            sessions.put(id, new Session(id, now, now, s.getMaxInactiveMs(), s.getMaxLifetimeMs()));
        }
    }

    public int purge(long now) {
        Iterator<Entry<String, Session>> iterator = sessions.entrySet().iterator();
        int count = 0;
        while (iterator.hasNext()) {
            Entry<String, Session> entry = iterator.next();
            if (entry.getValue().isExpired(now)) {
                iterator.remove();
                count++;
            }
        }
        return count;
    }

    public int size() {
        return sessions.size();
    }
}
