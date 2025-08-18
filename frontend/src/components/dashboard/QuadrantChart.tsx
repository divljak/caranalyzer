import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type ScatterPoint } from "@/lib/api";

interface QuadrantChartProps {
  scatterData?: ScatterPoint[];
}

export const QuadrantChart = ({ scatterData }: QuadrantChartProps) => {
  // Mock data points for the scatter plot if no real data
  const mockDataPoints = Array.from({ length: 50 }, (_, i) => ({
    x: Math.random() * 300,
    y: Math.random() * 32,
    color: `hsl(${Math.random() * 360}, 70%, 50%)`
  }));
  
  // Convert real scatter data to chart format using exact GitHub repo chart colors
  const realDataPoints = scatterData?.slice(0, 50).map((point, i) => ({
    x: (point.price / 1000) * 10, // Scale price to fit chart (max ~300)
    y: Math.min(point.days_on_market, 32), // Cap at 32 days
    color: point.mileage_bracket === '<50k km' ? 'hsl(12, 76%, 61%)' :      // chart-1: coral/salmon
           point.mileage_bracket === '50k-100k km' ? 'hsl(173, 58%, 39%)' :  // chart-2: teal
           point.mileage_bracket === '100k-150k km' ? 'hsl(43, 74%, 66%)' :  // chart-4: yellow
           'hsl(27, 87%, 67%)' // chart-5: orange for 150k+ km
  })) || [];
  
  const dataPoints = realDataPoints.length > 0 ? realDataPoints : mockDataPoints;

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
            <span>0 KM</span>
            <span>7k KM</span>
            <span>13k KM</span>
            <span>20k KM</span>
            <span>26k+ KM</span>
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
                  left: `${Math.min((point.x / 300) * 100, 95)}%`,
                  bottom: `${Math.min((point.y / 32) * 100, 95)}%`,
                }}
                title={scatterData?.[i] ? `${scatterData[i].model}: ${scatterData[i].price.toLocaleString()} KM, ${scatterData[i].days_on_market} days` : undefined}
              />
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
};