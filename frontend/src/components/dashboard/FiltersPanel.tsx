import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Filter } from "lucide-react";
import { type DashboardFilters } from "@/lib/api";

interface FiltersPanelProps {
  filters: Partial<DashboardFilters>;
  onFiltersChange: (filters: Partial<DashboardFilters>) => void;
}

export const FiltersPanel = ({ filters, onFiltersChange }: FiltersPanelProps) => {
  const handleFilterChange = (key: keyof DashboardFilters, value: any) => {
    onFiltersChange({
      ...filters,
      [key]: value,
    });
  };
  return (
    <Card className="w-80 p-6 h-fit">
      <div className="flex items-center gap-2 mb-6">
        <Filter className="h-5 w-5 text-primary" />
        <h2 className="text-lg font-semibold">Market Filters</h2>
        <Badge variant="secondary" className="ml-auto">Active</Badge>
      </div>
      
      <div className="space-y-6">
        {/* Date Range */}
        <div className="space-y-3">
          <Label className="text-sm font-medium">Date Range</Label>
          <div className="flex gap-2">
            <Button 
              variant={filters.timeframe_days === 30 ? "default" : "outline"} 
              size="sm" 
              className="flex-1"
              onClick={() => handleFilterChange('timeframe_days', 30)}
            >
              30 Days
            </Button>
            <Button 
              variant={(filters.timeframe_days === 60 || !filters.timeframe_days) ? "default" : "outline"} 
              size="sm" 
              className="flex-1"
              onClick={() => handleFilterChange('timeframe_days', 60)}
            >
              60 Days
            </Button>
            <Button 
              variant={filters.timeframe_days === 90 ? "default" : "outline"} 
              size="sm" 
              className="flex-1"
              onClick={() => handleFilterChange('timeframe_days', 90)}
            >
              90 Days
            </Button>
          </div>
        </div>

        <Separator />

        {/* Price Range */}
        <div className="space-y-3">
          <Label className="text-sm font-medium">Price Range (KM)</Label>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label className="text-xs text-muted-foreground">Min</Label>
              <Input 
                placeholder="0" 
                className="h-9" 
                type="number"
                value={filters.price_min || 0}
                onChange={(e) => handleFilterChange('price_min', parseInt(e.target.value) || 0)}
              />
            </div>
            <div>
              <Label className="text-xs text-muted-foreground">Max</Label>
              <Input 
                placeholder="25000" 
                className="h-9" 
                type="number"
                value={filters.max_price || 25000}
                onChange={(e) => handleFilterChange('max_price', parseInt(e.target.value) || 25000)}
              />
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
              <Select 
                value={filters.year_min?.toString() || "2000"}
                onValueChange={(value) => handleFilterChange('year_min', parseInt(value))}
              >
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
              <Select 
                value={filters.year_max?.toString() || "2024"}
                onValueChange={(value) => handleFilterChange('year_max', parseInt(value))}
              >
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
            <Button 
              variant={filters.transmission === 'Any' ? "default" : "outline"} 
              size="sm" 
              className="flex-1"
              onClick={() => handleFilterChange('transmission', 'Any')}
            >
              Any
            </Button>
            <Button 
              variant={filters.transmission === 'Auto' ? "default" : "outline"} 
              size="sm" 
              className="flex-1"
              onClick={() => handleFilterChange('transmission', 'Auto')}
            >
              Auto
            </Button>
            <Button 
              variant={filters.transmission === 'Manual' ? "default" : "outline"} 
              size="sm" 
              className="flex-1"
              onClick={() => handleFilterChange('transmission', 'Manual')}
            >
              Manual
            </Button>
          </div>
        </div>

        <Separator />

        {/* Min Listings */}
        <div className="space-y-3">
          <Label className="text-sm font-medium">Min Listings per Model</Label>
          <Select 
            value={filters.min_listings?.toString() || "5"}
            onValueChange={(value) => handleFilterChange('min_listings', parseInt(value))}
          >
            <SelectTrigger className="h-9">
              <SelectValue placeholder="5" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="2">2+ listings</SelectItem>
              <SelectItem value="3">3+ listings</SelectItem>
              <SelectItem value="5">5+ listings</SelectItem>
              <SelectItem value="10">10+ listings</SelectItem>
              <SelectItem value="15">15+ listings</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <Separator />

        {/* Reset Button */}
        <Button 
          variant="outline" 
          className="w-full"
          onClick={() => {
            onFiltersChange({
              timeframe_days: 60,
              max_price: 25000,
              price_min: 0,
              year_min: 2000,
              year_max: 2024,
              transmission: 'Any',
              min_listings: 5
            });
          }}
        >
          Reset to Defaults
        </Button>
      </div>
    </Card>
  );
};