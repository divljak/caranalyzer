import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Filter } from "lucide-react";

export const FiltersPanel = () => {
  return (
    <Card className="w-80 p-6 h-fit">
      <div className="flex items-center gap-2 mb-6">
        <h2 className="text-lg font-semibold">Market Filters</h2>
      </div>
      
      <div className="space-y-6">
        {/* Date Range */}
        <div className="space-y-3">
          <Label className="text-sm font-medium">Date Range</Label>
          <div className="flex gap-2">
            <Button variant="default" size="sm" className="flex-1">30 Days</Button>
            <Button variant="outline" size="sm" className="flex-1">60 Days</Button>
            <Button variant="outline" size="sm" className="flex-1">90 Days</Button>
          </div>
        </div>

        <Separator />

        {/* Price Range */}
        <div className="space-y-3">
          <Label className="text-sm font-medium">Price Range (KM)</Label>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label className="text-xs text-muted-foreground">Min</Label>
              <Input placeholder="20000" className="h-9" />
            </div>
            <div>
              <Label className="text-xs text-muted-foreground">Max</Label>
              <Input placeholder="250000" className="h-9" />
            </div>
          </div>
        </div>

        <Separator />

        {/* Year Range */}
        <div className="space-y-3">
          <Label className="text-sm font-medium">Year Range</Label>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label className="text-xs text-muted-foreground">From</Label>
              <Select>
                <SelectTrigger className="h-9">
                  <SelectValue placeholder="2000" />
                </SelectTrigger>
                <SelectContent>
                  {Array.from({ length: 25 }, (_, i) => 2000 + i).map(year => (
                    <SelectItem key={year} value={year.toString()}>{year}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-xs text-muted-foreground">To</Label>
              <Select>
                <SelectTrigger className="h-9">
                  <SelectValue placeholder="2024" />
                </SelectTrigger>
                <SelectContent>
                  {Array.from({ length: 25 }, (_, i) => 2000 + i).map(year => (
                    <SelectItem key={year} value={year.toString()}>{year}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        <Separator />

        {/* Transmission */}
        <div className="space-y-3">
          <Label className="text-sm font-medium">Transmission</Label>
          <div className="flex gap-2">
            <Button variant="default" size="sm" className="flex-1">Any</Button>
            <Button variant="outline" size="sm" className="flex-1">Auto</Button>
            <Button variant="outline" size="sm" className="flex-1">Manual</Button>
          </div>
        </div>

        <Separator />

        {/* Apply Filters Button */}
        <Button className="w-full" size="lg">
          Apply Filters
        </Button>
      </div>
    </Card>
  );
};