import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const QuadrantChart = () => {
  // Mock data points for the scatter plot
  const dataPoints = Array.from({ length: 50 }, (_, i) => ({
    x: Math.random() * 300,
    y: Math.random() * 32,
    color: `hsl(${Math.random() * 360}, 70%, 50%)`
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle>Market Quadrant Analysis</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="relative w-full h-80 bg-muted/30 rounded-lg overflow-hidden">
          {/* Y-axis */}
          <div className="absolute left-0 top-0 h-full w-12 flex flex-col justify-between text-xs text-muted-foreground py-4">
            <span>32 days</span>
            <span>24 days</span>
            <span>16 days</span>
            <span>8 days</span>
            <span>0 days</span>
          </div>
          
          {/* X-axis */}
          <div className="absolute bottom-0 left-12 right-0 h-8 flex justify-between items-center text-xs text-muted-foreground px-4">
            <span>0k KM</span>
            <span>7k KM</span>
            <span>13k KM</span>
            <span>20k KM</span>
            <span>26k KM</span>
          </div>

          {/* Grid lines */}
          <div className="absolute left-12 top-4 right-4 bottom-8">
            {/* Horizontal grid lines */}
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={`h-${i}`}
                className="absolute w-full border-t border-border/40"
                style={{ top: `${i * 25}%` }}
              />
            ))}
            {/* Vertical grid lines */}
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={`v-${i}`}
                className="absolute h-full border-l border-border/40"
                style={{ left: `${i * 25}%` }}
              />
            ))}
          </div>

          {/* Data points */}
          <div className="absolute left-12 top-4 right-4 bottom-8">
            {dataPoints.map((point, i) => (
              <div
                key={i}
                className="absolute w-2 h-2 rounded-full transition-transform hover:scale-150"
                style={{
                  backgroundColor: point.color,
                  left: `${(point.x / 300) * 100}%`,
                  bottom: `${(point.y / 32) * 100}%`,
                }}
              />
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
};