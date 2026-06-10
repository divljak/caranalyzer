import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Crown, ExternalLink, Eye } from "lucide-react";
import { type PopularModel } from "@/lib/api";

interface PopularModelsTableProps {
  models: PopularModel[];
}

export const PopularModelsTable = ({ models }: PopularModelsTableProps) => {
  return (
    <div className="space-y-4 mt-6">
      <div className="space-y-2">
        <h2 className="text-xl font-semibold">🏆 Most Popular Cars on OLX.ba</h2>
        <p className="text-sm text-muted-foreground">
          Models with the most listings and buyer interest in your selected filters — the heart of the market.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Crown className="h-5 w-5 text-primary" />
            Market Popularity Ranking
          </CardTitle>
        </CardHeader>
        <CardContent>
          {models.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No model data available with current filters.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-12">#</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead className="text-right">Listings</TableHead>
                  <TableHead className="text-right">Avg Views</TableHead>
                  <TableHead className="text-right">Avg Price</TableHead>
                  <TableHead className="text-right">Avg Days Listed</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {models.map((m) => (
                  <TableRow
                    key={m.rank}
                    className="cursor-pointer"
                    onClick={() => window.open(m.search_url, "_blank", "noopener")}
                  >
                    <TableCell>
                      <Badge variant={m.rank <= 3 ? "default" : "outline"} className="text-xs">
                        #{m.rank}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-medium">{m.model}</TableCell>
                    <TableCell className="text-right font-semibold">{m.listings_count}</TableCell>
                    <TableCell className="text-right">
                      <span className="inline-flex items-center gap-1">
                        <Eye className="h-3 w-3 text-muted-foreground" />
                        {m.avg_views.toLocaleString()}
                      </span>
                    </TableCell>
                    <TableCell className="text-right">{m.avg_price.toLocaleString()} KM</TableCell>
                    <TableCell className="text-right">{m.avg_days_on_market.toFixed(0)} days</TableCell>
                    <TableCell>
                      <ExternalLink className="h-3 w-3 text-muted-foreground" />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default PopularModelsTable;
