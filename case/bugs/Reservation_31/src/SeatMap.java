/** Seat occupancy map with capacity and quota helpers. */
public final class SeatMap {
    private final boolean[] seats;

    public SeatMap(int capacity) {
        seats = new boolean[capacity];
    }

    public boolean occupy(int index) {
        if (index < 0 || index >= seats.length || seats[index]) {
            return false;
        }
        seats[index] = true;
        return true;
    }

    public boolean occupied(int index) {
        return index >= 0 && index < seats.length && seats[index];
    }

    public int remaining() {
        int count = 0;
        for (boolean seat : seats) {
            if (!seat) {
                count++;
            }
        }
        return count;
    }
}
